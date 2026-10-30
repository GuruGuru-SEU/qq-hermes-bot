import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from qqhermes.store import Store
from qqhermes.live import LiveInbox, boundary
from qqhermes.conversation import conversation
import test_integration as fixtures


def event(mid, user=42, text='draw a cat'):
    return {'message_id':mid, 'group_id':1, 'user_id':user, 'time':time.time(),
            'sender':{'nickname':str(user)}, 'message':[{'type':'text','data':{'text':text}}]}


class LiveLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'transport.sqlite3')
        self.live=LiveInbox(self.store)
        self.store.enqueue(1,event('a'),[]);self.store.finish(1,'a','processing')
        self.live.start(1,event('a'))

    def follow(self, mid, user=42, text='red'):
        self.store.enqueue(1,event(mid,user,text),[])
        return self.live.take(1,'a')

    def test_sender_preserved_and_only_same_chat_is_consumed(self):
        self.store.enqueue(2,event('other'),[])
        self.follow('b',43)
        state=self.live.state(1,'a')
        self.assertEqual(state['unclassified'],['b'])
        self.assertEqual(state['requests'][1]['owner'],'43')
        self.assertFalse(self.store.enqueue(1,event('b',43),[]))
        self.assertEqual(self.live.take(1,'a'),[])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT status FROM triggers WHERE group_id=2").fetchone()[0],'queued')

    def test_other_member_cannot_override_cancel_or_approve(self):
        self.follow('b',43)
        for relation in ('refine','cancel','replace','approve'):
            with self.assertRaises(PermissionError):
                self.live.resolve(1,'a',{'message_id':'b','task_id':'a','relation':relation})
        self.live.resolve(1,'a',{'message_id':'b','task_id':'a','relation':'proposal'})
        self.follow('c',42,'Use Bob’s material, still below 50 yuan')
        self.live.resolve(1,'a',{'message_id':'c','task_id':'a','relation':'approve','proposal_id':'b'})
        self.assertEqual(self.live.state(1,'a')['unclassified'],[])

    def test_compatible_contribution_and_separate_tasks_keep_owners(self):
        self.follow('b',43,'add a bell')
        self.live.resolve(1,'a',{'message_id':'b','task_id':'a','relation':'contribute'})
        self.follow('c',43,'write a poem')
        args={'message_id':'c','relation':'new','objective':'poem'}
        self.live.resolve(1,'a',args);self.live.resolve(1,'a',args)
        self.assertEqual({r['mid']:r['owner'] for r in self.live.state(1,'a')['open_tasks']},{'a':'42','c':'43'})

    def test_replace_and_cancel_only_own_task(self):
        self.follow('b',42,'instead write a poem')
        self.live.resolve(1,'a',{'message_id':'b','task_id':'a','relation':'replace'})
        tasks={t['mid']:t['state'] for t in self.live.state(1,'a')['tasks']}
        self.assertEqual(tasks,{'a':'superseded','b':'working'})

    def test_owner_can_approve_proposal_in_a_later_foreground_turn(self):
        self.follow('b',43,'material costs ten yuan more')
        self.live.resolve(1,'a',{'message_id':'b','task_id':'a','relation':'proposal'})
        self.live.finish(1,'a','success')
        approval=event('c',42,'Use Bob’s suggestion, keep the 50 yuan budget')
        self.store.enqueue(1,approval,[]);self.store.finish(1,'c','processing')
        self.live.start(1,approval)
        self.assertEqual(self.live.state(1,'c')['pending_proposals'][0]['message_id'],'b')
        state=self.live.resolve(1,'c',{'message_id':'c','task_id':'a','relation':'approve','proposal_id':'b'})
        self.assertEqual([r['mid'] for r in state['open_tasks']],['a'])
        self.assertEqual(state['requests'][0]['owner'],'42')
        self.assertFalse(state['pending_proposals'])

    def test_consumed_requests_do_not_inflate_status_queue(self):
        self.store.enqueue(1,event('b'),[])
        self.assertEqual(self.store.queued_count(1),1)
        self.live.take(1,'a')
        self.assertEqual(self.store.queued_count(1),0)

    def test_commands_are_fifo_barriers_and_restart_does_not_repeat_consumed_actions(self):
        self.follow('b')
        self.store.enqueue(1,event('reset',text='/new'),[])
        self.store.enqueue(1,event('later'),[])
        self.assertFalse(self.live.take(1,'a'))
        recovered=self.store.recover_inbox()
        self.assertEqual([e['message_id'] for e,_ in recovered],['reset','later'])
        self.assertEqual(self.live.state(1,'a')['tasks'][0]['state'],'interrupted')

    def test_voice_waits_for_normal_transcription_instead_of_being_swallowed(self):
        voice=event('voice');voice['message']=[{'type':'record','data':{'file':'test.silk'}}]
        self.store.enqueue(1,voice,[])
        self.store.enqueue(1,event('later'),[])
        self.assertEqual(self.live.take(1,'a'),[])
        self.assertEqual(self.store.queued_count(1),2)

    def test_late_followup_reopens_same_model_loop_without_running_stale_output(self):
        self.store.enqueue(1,event('b',43,'new task'),[])
        messages=[{'role':'user','content':'original'}]
        with patch('qqhermes.live.scope',return_value=(conversation(1),'a')), \
             patch('qqhermes.live.root',return_value=Path(self.temp.name)), \
             patch('qqhermes.live.describe',side_effect=lambda c,e:e):
            self.assertTrue(boundary(SimpleNamespace(),messages,final=True))
        self.assertEqual(len(messages),2)
        self.assertIn('43',messages[-1]['content'])
        self.assertEqual(self.live.take(1,'a'),[])


class LiveAdapterTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.AdapterTests.asyncSetUp
    asyncTearDown=fixtures.AdapterTests.asyncTearDown
    event=fixtures.AdapterTests.event

    async def test_consumed_followups_do_not_get_a_second_model_turn(self):
        started=asyncio.Event();release=asyncio.Event()
        async def handle(e):
            self.events.append(e);e._gateway_accepted=True
            started.set();await release.wait()
            await self.adapter.on_processing_complete(e,SimpleNamespace(value='success'))
        self.adapter.handle_message=handle
        self.adapter.ingest(self.event(1));await started.wait()
        self.adapter.ingest(self.event(2));self.adapter.ingest(self.event(3))
        live=LiveInbox(self.adapter.store)
        self.assertEqual(len(live.take(1,'snowluma:1')),2)
        release.set();await asyncio.wait_for(self.adapter.queues[1].join(),3)
        self.assertEqual(len(self.events),1)
        with self.adapter.store.db() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM triggers')],['success']*3)

    async def test_stop_rejects_another_member_and_accepts_configured_operator(self):
        turn={'mid':'a','owner':'42'}
        self.assertFalse(self.adapter.can_stop(1,43,turn))
        self.assertTrue(self.adapter.can_stop(1,42,turn))
        self.adapter.cfg['group_operators']={'1':[43]}
        self.assertTrue(self.adapter.can_stop(1,43,turn))

    async def test_reply_to_bot_is_addressed_but_other_group_chatter_is_not(self):
        self.adapter.archive(1).save({'message_id':'snowluma:9','user_id':99,'time':time.time(),
                                     'message':[{'type':'text','data':{'text':'hello'}}]})
        self.adapter.ingest(self.event(1,at=False))
        self.assertFalse(self.adapter.queues)
        self.adapter.ingest(self.event(2,at=False,segments=[{'type':'reply','data':{'id':'snowluma:9'}},
                            {'type':'text','data':{'text':'make it red'}}]))
        await self.adapter.queues[1].join()
        self.assertEqual(len(self.events),1)

    async def test_result_delivery_is_idempotent_and_acks_do_not_complete_task(self):
        live=LiveInbox(self.adapter.store)
        data=self.event(1);mid=data['message_id']
        self.adapter.store.enqueue(1,data,[]);self.adapter.store.finish(1,mid,'processing');live.start(1,data)
        self.adapter.api.return_value={'message_id':'snowluma:99'}
        args={'chat_id':'1','root_mid':mid,'task_id':mid,'message_id':mid,'kind':'acknowledgement','text':'收到，改成红色。'}
        result=await self.adapter.report_work(args);self.assertTrue(result['success'])
        self.assertEqual(len(live.state(1,mid)['open_tasks']),1)
        args.update(kind='result',text='画好了。')
        self.assertTrue((await self.adapter.report_work(args))['success'])
        self.assertTrue((await self.adapter.report_work(args))['success'])
        self.assertEqual(self.adapter.api.await_count,2)
        self.assertFalse(live.state(1,mid)['open_tasks'])

    async def test_unknown_delivery_is_not_retried_and_pending_input_blocks_send(self):
        live=LiveInbox(self.adapter.store);data=self.event(1);mid=data['message_id']
        self.adapter.store.enqueue(1,data,[]);self.adapter.store.finish(1,mid,'processing');live.start(1,data)
        args={'chat_id':'1','root_mid':mid,'kind':'result','text':'done'}
        self.adapter.store.enqueue(1,self.event(2),[])
        self.assertEqual((await self.adapter.report_work(args))['delivery'],'not_attempted')
        live.take(1,mid)
        live.resolve(1,mid,{'message_id':'snowluma:2','task_id':mid,'relation':'refine'})
        self.adapter.api.side_effect=TimeoutError()
        self.assertFalse((await self.adapter.report_work(args))['success'])
        self.assertFalse((await self.adapter.report_work(args))['success'])
        self.adapter.api.assert_awaited_once()

    async def test_progress_rate_limit_and_final_recap_suppression(self):
        live=LiveInbox(self.adapter.store);data=self.event(1);mid=data['message_id']
        self.adapter.store.enqueue(1,data,[]);self.adapter.store.finish(1,mid,'processing');live.start(1,data)
        self.adapter.api.return_value={'message_id':'snowluma:99'}
        args={'chat_id':'1','root_mid':mid,'kind':'progress','text':'Finished downloading; processing now.'}
        self.assertTrue((await self.adapter.report_work(args))['success'])
        args['text']='Processing finished; checking the output.'
        self.assertFalse((await self.adapter.report_work(args))['success'])
        with self.adapter.store.db() as db:db.execute('UPDATE work_reports SET updated=0')
        self.assertTrue((await self.adapter.report_work(args))['success'])
        args.update(kind='result',text='Done.')
        self.assertTrue((await self.adapter.report_work(args))['success'])
        event=SimpleNamespace(source=SimpleNamespace(chat_id='1'),message_id=mid,text='request')
        result,_=await self.adapter.send_final_ledgered(event,'session','Done again.',{},reply_to=mid)
        self.assertTrue(result.success)
        self.assertEqual(self.adapter.api.await_count,3)
