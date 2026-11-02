import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_integration as fixtures
from qqhermes.conversation import conversation
from qqhermes.live import LiveInbox, boundary, release, finish_ready, delivered_turn


class SilenceReceiptTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AdapterTests.asyncSetUp
    asyncTearDown = fixtures.AdapterTests.asyncTearDown
    event = fixtures.AdapterTests.event

    def start(self):
        self.live = LiveInbox(self.adapter.store)
        data = self.event(1);self.mid = data['message_id']
        self.adapter.store.enqueue(1, data, [])
        self.adapter.store.finish(1, self.mid, 'processing')
        self.live.start(1, data)
        self.source = SimpleNamespace(platform='qq_onebot', chat_id='1', message_id=self.mid)
        self.adapter.api.return_value = {'message_id':'snowluma:99'}
        self.addCleanup(release, 1, self.mid)

    def completion(self):
        with patch('qqhermes.live.scope', return_value=(conversation(1), self.mid)), patch('qqhermes.live.root', return_value=self.adapter.store.path.parent):
            return finish_ready(SimpleNamespace(), [])

    def delivered(self, completion, **result):
        with patch('qqhermes.live.root', return_value=self.adapter.store.path.parent):
            return delivered_turn(self.source, {'qq_completion':completion, **result})

    async def test_greeting_ack_then_main_result_needs_receipted_completion(self):
        self.start()
        self.adapter.store.enqueue(1, self.event(2), [])
        self.live.take(1, self.mid)
        self.live.resolve(1, self.mid, {'message_id':'snowluma:2','relation':'new'})
        greeting = await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,
            'task_id':'snowluma:2','kind':'acknowledgement','text':'Hello.'})
        self.assertTrue(greeting['success'])
        self.assertEqual(self.adapter.api.call_args.args[1]['message'][0]['data']['id'], 'snowluma:2')
        await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Sphere ready.'})
        self.assertIsNone(self.completion())  # Main done, greeting task still open.
        agent=SimpleNamespace();messages=[]
        with patch('qqhermes.live.scope', return_value=(conversation(1), self.mid)), patch('qqhermes.live.root', return_value=self.adapter.store.path.parent):
            self.assertTrue(boundary(agent, messages, final=True, response='[SILENT]'))
        self.assertIn(greeting['id'], messages[-1]['content'])
        self.assertIn('qq_work finish', messages[-1]['content'])
        args={'task_id':'snowluma:2','report_id':greeting['id']}
        self.live.complete(1, self.mid, args)
        self.live.complete(1, self.mid, args)  # Idempotent, no network send.
        completion=self.completion();self.assertTrue(self.delivered(completion))
        event=SimpleNamespace(source=self.source,message_id=self.mid,text='sphere')
        result,_=await self.adapter.send_final_ledgered(event,'session','[SILENT]',{},reply_to=self.mid)
        self.assertTrue(result.success)
        self.assertEqual(self.adapter.api.await_count,2)

    async def test_no_completion_without_matching_confirmed_receipt(self):
        self.start()
        report=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'acknowledgement','text':'Hello.'})
        args={'task_id':self.mid,'report_id':report['id']}
        for status in ('unknown','attempting','not_attempted'):
            with self.adapter.store.db() as db:
                db.execute('UPDATE work_reports SET status=? WHERE id=?',(status,report['id']))
            with self.assertRaises(ValueError):self.live.complete(1,self.mid,args)
        with self.adapter.store.db() as db:
            db.execute("UPDATE work_reports SET status='sent'")
        with self.assertRaises(ValueError):self.live.complete(2,self.mid,args)
        with self.assertRaises(ValueError):self.live.complete(1,self.mid,{**args,'task_id':'other'})
        self.adapter.store.enqueue(1,self.event(2),[])
        with self.assertRaises(ValueError):self.live.complete(1,self.mid,args)
        self.live.take(1,self.mid)
        with self.assertRaises(ValueError):self.live.complete(1,self.mid,args)

    async def test_already_settled_task_cannot_send_a_second_result(self):
        self.start()
        report=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'acknowledgement','text':'Hello.'})
        self.live.complete(1,self.mid,{'task_id':self.mid,'report_id':report['id']})
        repeated=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Hello again.'})
        self.assertFalse(repeated['success'],repeated)
        self.assertEqual(repeated['delivery'],'not_attempted')
        self.assertEqual(self.adapter.api.await_count,1)

    async def test_cancelled_task_is_rejected_before_network_send(self):
        self.start()
        followup=self.event(2)
        self.adapter.store.enqueue(1,followup,[]);self.live.take(1,self.mid)
        self.live.resolve(1,self.mid,{'message_id':followup['message_id'],'relation':'cancel'})
        result=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Should not be sent.'})
        self.assertFalse(result['success'],result)
        self.assertEqual(result['delivery'],'not_attempted')
        self.adapter.api.assert_not_awaited()

    async def test_silence_does_not_hide_failed_unanswered_or_other_turns(self):
        self.start()
        self.assertFalse(self.delivered(None))
        report=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'acknowledgement','text':'Hello.'})
        self.live.complete(1,self.mid,{'task_id':self.mid,'report_id':report['id']})
        completion=self.completion();self.assertTrue(self.delivered(completion))
        self.assertFalse(self.delivered(completion,failed=True))
        self.assertFalse(self.delivered(completion,queued_terminal_inbound_id='other'))
        self.source.message_id='other';self.assertFalse(self.delivered(completion))
        self.source.message_id=self.mid;self.source.platform='telegram';self.assertFalse(self.delivered(completion))
        self.source.platform='qq_onebot'
        self.adapter.store.enqueue(1,self.event(2),[])
        self.assertTrue(self.delivered(completion))  # After seal: next FIFO turn.
        self.assertEqual(self.live.take(1,self.mid),[])
        self.assertEqual(self.adapter.store.queued_count(1),1)

    async def test_normal_final_answer_can_finish_one_open_task(self):
        self.start()
        with patch('qqhermes.live.scope',return_value=(conversation(1),self.mid)), patch('qqhermes.live.root',return_value=self.adapter.store.path.parent):
            self.assertFalse(boundary(SimpleNamespace(),[],final=True,response='Here is your answer.'))
            self.assertTrue(boundary(SimpleNamespace(),[],final=True,response='[SILENT]'))

    async def test_plain_final_and_tool_final_share_receipts(self):
        self.start()
        event=SimpleNamespace(source=self.source,message_id=self.mid,text='hello')
        result,_=await self.adapter.send_final_ledgered(event,'session','Hello.',{},reply_to=self.mid)
        self.assertTrue(result.success)
        self.assertEqual(self.adapter.api.await_count,1)
        state=self.live.state(1,self.mid)
        self.assertFalse(state['open_tasks'])
        self.assertEqual(state['reports'][0]['kind'],'result')
        with self.adapter.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_completions').fetchone()[0],1)
        await self.adapter.send_final_ledgered(event,'session','Duplicate.',{},reply_to=self.mid)
        self.assertEqual(self.adapter.api.await_count,1)

    async def test_arrival_before_turn_seal_prevents_completion(self):
        self.start()
        await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Done.'})
        self.adapter.store.enqueue(1,self.event(2),[])
        self.assertIsNone(self.completion())
        self.assertEqual(len(self.live.take(1,self.mid)),1)
        self.assertIsNone(self.completion())

    async def test_failed_send_never_settles_task(self):
        self.start();self.adapter.api.side_effect=TimeoutError()
        sent=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Done.'})
        self.assertFalse(sent['success'])
        self.assertIsNone(self.completion())
        with self.adapter.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_completions').fetchone()[0],0)

    async def test_correction_after_sent_result_gets_new_receipt_before_turn_closes(self):
        self.start()
        original=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Blue.'})
        self.adapter.store.enqueue(1,self.event(2),[])
        self.live.take(1,self.mid)
        self.live.resolve(1,self.mid,{'message_id':'snowluma:2','relation':'refine'})
        self.assertIsNone(self.completion())
        revised=await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Red.'})
        self.assertTrue(revised['success'])
        self.assertNotEqual(original['id'],revised['id'])
        self.assertTrue(self.delivered(self.completion()))
        self.assertEqual(self.adapter.api.await_count,2)

    async def test_native_gateway_accepts_only_committed_completion(self):
        from gateway.run_turn_persistence import GatewayTurnPersistenceMixin
        import sys
        import qqhermes.live as live
        self.start()
        await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Delivered.'})
        completion=self.completion()
        result={'qq_completion':completion,'completed':True,'final_response':'Delivered.','messages':[]}
        runner=SimpleNamespace(_is_intentional_silence=lambda *a:False)
        with patch.dict(sys.modules,{'_qq_hermes_live_inbox':live}), patch('qqhermes.live.root',return_value=self.adapter.store.path.parent):
            shaped=await GatewayTurnPersistenceMixin._hmwa_shape_agent_response(
                runner,result,self.source,[],None,None,None,None,None,'qq_onebot',0)
        self.assertEqual(shaped,('',False,[]))
        self.assertTrue(result['already_sent'])
        self.assertTrue(result['media_already_delivered'])

    async def test_waiting_for_user_is_settled_but_progress_alone_is_not(self):
        self.start()
        await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'progress','text':'Working.'})
        self.assertIsNone(self.completion())
        await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','outcome':'waiting','text':'Which size?'})
        self.assertTrue(self.delivered(self.completion()))

    async def test_later_turn_approval_reports_against_its_own_input(self):
        self.start()
        bob=self.event(2);bob['user_id']=43
        self.adapter.store.enqueue(1,bob,[]);self.live.take(1,self.mid)
        self.live.resolve(1,self.mid,{'message_id':bob['message_id'],'relation':'proposal'})
        await self.adapter.report_work({'chat_id':'1','root_mid':self.mid,'kind':'result','text':'Original budget.'})
        self.live.finish(1,self.mid,'success')
        approval=self.event(3);new=approval['message_id']
        self.adapter.store.enqueue(1,approval,[]);self.adapter.store.finish(1,new,'processing');self.live.start(1,approval)
        self.live.resolve(1,new,{'message_id':new,'relation':'approve','task_id':self.mid,'proposal_id':bob['message_id']})
        result=await self.adapter.report_work({'chat_id':'1','root_mid':new,'task_id':self.mid,'kind':'result','text':'Approved update.'})
        self.assertTrue(result['success'],result)
        with self.adapter.store.db() as db:
            row=db.execute('SELECT source FROM work_reports WHERE id=?',(result['id'],)).fetchone()
        self.assertEqual(row['source'],new)
