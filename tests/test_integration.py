import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,AsyncMock

from qqhermes.store import Store
from qqhermes.history import History,query


class ArchiveTests(unittest.TestCase):
    def test_history_persists_and_filters_by_user_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'history.sqlite3';h=History(path)
            h.save({'message_id':'snowluma:1','time':1792339200,'user_id':42,'sender':{'nickname':'member'},'message':[{'type':'text','data':{'text':'remember this'}}]})
            del h
            result=query(path,user=42,contains='remember')
            self.assertEqual(result['total'],1)
            self.assertEqual(query(path,user=43)['total'],0)

    def test_consent_is_same_user_later_message_exact_task_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'transport.sqlite3');args={'goal':'compute'}
            p=s.proposal(1,'42','m1',args)
            for group,user,mid in [(2,'42','m2'),(1,'43','m2'),(1,'42','m1')]:
                with self.assertRaises(PermissionError):s.approve(p,group,user,mid)
            self.assertEqual(s.approve(p,1,'42','m2'),args)
            self.assertFalse(s.consume(1,'42','m2',{'goal':'changed'}))
            self.assertTrue(s.consume(1,'42','m2',args))
            self.assertFalse(s.consume(1,'42','m2',args))

    def test_trigger_dedup_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'transport.sqlite3'
            self.assertTrue(Store(path).claim(1,'mid'))
            self.assertFalse(Store(path).claim(1,'mid'))
            self.assertTrue(Store(path).claim(2,'mid'))


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from gateway.config import PlatformConfig
        from qqhermes.adapter import QQAdapter
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        cfg={'bot_qq':99,'trusted_groups':[1,2],'trusted_users':[1,42,43],'gateway':'snowluma','port':0}
        path=self.root/'config.json';path.write_text(json.dumps(cfg))
        self.env=patch.dict(os.environ,{'QQ_STATE':str(self.root/'state'),'QQ_CONFIG':str(path),'ONEBOT_TOKEN':'test-token'})
        self.env.start()
        from gateway.platform_registry import platform_registry,PlatformEntry
        platform_registry.register(PlatformEntry(name='qq_onebot',label='QQ test',adapter_factory=QQAdapter,check_fn=lambda:True))
        self.adapter=QQAdapter(PlatformConfig(enabled=True))
        self.adapter.receipt=AsyncMock();self.adapter.api=AsyncMock(return_value={})
        self.events=[]
        async def handle(event):
            self.events.append(event);event._gateway_accepted=True
            from types import SimpleNamespace
            await self.adapter.on_processing_complete(event,SimpleNamespace(value='success'))
        self.adapter.handle_message=handle

    async def asyncTearDown(self):
        for t in list(self.adapter.tasks):t.cancel()
        await asyncio.gather(*self.adapter.tasks,return_exceptions=True)
        self.env.stop();self.tmp.cleanup()

    def event(self,mid,group=1,at=True,segments=None):
        return {'group_id':group,'user_id':42,'message_id':f'snowluma:{mid}','time':time.time(),
            'sender':{'nickname':'member'},'message':segments if segments is not None else
            ([{'type':'at','data':{'qq':'99'}}] if at else [])+[{'type':'text','data':{'text':f'say {mid}'}}]}

    async def test_burst_is_three_separate_ordered_requests(self):
        for i in range(1,4):self.adapter.ingest(self.event(i))
        await asyncio.wait_for(self.adapter.queues[1].join(),3)
        self.assertEqual([e.text for e in self.events],['say 1','say 2','say 3'])
        self.assertTrue(all(not self.adapter._is_queue_text_debounce_candidate(e) for e in self.events))
        self.adapter.ingest(self.event(1))
        self.assertEqual(self.adapter.queues[1].qsize(),0)

    async def test_same_second_dm_burst_is_distinct_even_if_previous_turn_used_file(self):
        # The gateway can deliver an instruction before its file and follow-up,
        # all with the same QQ timestamp. Preserve arrival order, not numeric IDs.
        stamp=int(time.time())
        for mid,text,segments in [(867,'Install the provided skill',None),
                (138,'', [{'type':'file','data':{'name':'skill.zip','file_id':'file-1'}}]),
                (-215,'Generate today report',None)]:
            event=self.event(mid,segments=segments)
            event.update(message_type='private',sub_type='friend',time=stamp)
            if segments is None:event['message']=[{'type':'text','data':{'text':text}}]
            self.adapter.ingest(event)
            self.adapter.ingest(event)  # Real repeated delivery must not add a turn.
        await asyncio.wait_for(self.adapter.queues['dm-42'].join(),3)
        self.assertEqual([e.message_id for e in self.events],['snowluma:867','snowluma:138','snowluma:-215'])
        payloads=[json.loads(e.channel_context.split('\n',1)[1]) for e in self.events]
        for e,payload in zip(self.events,payloads):
            self.assertEqual(payload['bridge_delivery']['delivery'],'first_admission')
            self.assertEqual(payload['bridge_delivery']['current_message_id'],e.message_id)
        self.assertEqual(payloads[0]['bridge_delivery']['earlier_turns'],[])
        self.assertEqual(set(payloads[0]['bridge_delivery']['other_pending_message_ids']),{'snowluma:138','snowluma:-215'})
        self.assertEqual(payloads[1]['bridge_delivery']['earlier_turns'],[{'message_id':'snowluma:867','outcome':'success'}])
        self.assertEqual(payloads[1]['current_attachments']['files'][0]['name'],'skill.zip')
        self.assertEqual(payloads[2]['bridge_delivery']['other_pending_message_ids'],[])
        self.assertEqual([r['message_id'] for r in payloads[2]['bridge_delivery']['earlier_turns']],['snowluma:867','snowluma:138'])

    async def test_startup_restore_keeps_burst_in_bridge_fifo(self):
        from types import SimpleNamespace
        runner=SimpleNamespace(_startup_restore_in_progress=True)
        self.adapter.gateway_runner=runner
        for i in range(1,4):self.adapter.ingest(self.event(i))
        await asyncio.sleep(.15)
        self.assertEqual(self.events,[])
        runner._startup_restore_in_progress=False
        await asyncio.wait_for(self.adapter.queues[1].join(),3)
        self.assertEqual([e.text for e in self.events],['say 1','say 2','say 3'])

    async def test_next_request_waits_for_native_cleanup_after_completion_hook(self):
        from types import SimpleNamespace
        cleanup=asyncio.Event()
        async def handle(event):
            self.events.append(event);event._gateway_accepted=True
            if len(self.events)==1:
                async def native_turn():
                    await self.adapter.on_processing_complete(event,SimpleNamespace(value='success'))
                    await cleanup.wait()
                self.adapter._session_tasks[self.adapter._event_session_key(event)]=asyncio.create_task(native_turn())
            else:await self.adapter.on_processing_complete(event,SimpleNamespace(value='success'))
        self.adapter.handle_message=handle
        self.adapter.ingest(self.event(1));self.adapter.ingest(self.event(2))
        await asyncio.sleep(.1)
        self.assertEqual([e.text for e in self.events],['say 1'])
        cleanup.set()
        await asyncio.wait_for(self.adapter.queues[1].join(),3)
        self.assertEqual([e.text for e in self.events],['say 1','say 2'])

    async def test_nonmention_image_stays_in_next_mention_context(self):
        image=self.event(10,at=False,segments=[{'type':'image','data':{'url':'https://qpic.cn/test'}}])
        self.adapter.ingest(image)
        archive=self.adapter.archive(1)
        path=self.root/'picture.png';path.write_bytes(b'fixture')
        archive.image_result('snowluma:10',0,'ready',path)
        self.adapter.images.cache_message=AsyncMock()
        request=self.event(11);request['message'][-1]['data']['text']='这张图里是什么？'
        self.adapter.ingest(request)
        await asyncio.wait_for(self.adapter.queues[1].join(),3)
        self.assertIn(str(path),self.events[0].media_urls)
        self.assertIn('snowluma:10',self.events[0].channel_context)
        self.assertEqual(self.events[0].source.chat_id,'1')

    async def test_busy_group_waits_for_completion_other_group_runs(self):
        async def hold(event):
            self.events.append(event);event._gateway_accepted=True
        self.adapter.handle_message=hold
        self.adapter.ingest(self.event(1));self.adapter.ingest(self.event(2))
        self.adapter.ingest(self.event(3,group=2))
        await asyncio.sleep(.1)
        self.assertEqual({e.text for e in self.events},{'say 1','say 3'})
        from types import SimpleNamespace
        await self.adapter.on_processing_complete(self.events[0],SimpleNamespace(value='success'))
        await asyncio.sleep(.1)
        self.assertEqual([e.text for e in self.events if e.source.chat_id=='1'],['say 1','say 2'])

    async def test_untrusted_ignored_status_bypasses_agent(self):
        self.adapter.ingest(self.event(1,group=3))
        self.assertFalse(self.adapter.archives)
        self.adapter.send=AsyncMock()
        e=self.event(2);e['message'][-1]['data']['text']='please #status now'
        self.adapter.ingest(e)
        await asyncio.gather(*list(self.adapter.tasks))
        self.assertFalse(self.events);self.adapter.send.assert_awaited_once()

    async def test_status_inspects_busy_group_without_queueing_or_interrupting(self):
        from qqhermes import activity
        from types import SimpleNamespace
        entered=asyncio.Event();sent=asyncio.Event();responses=[]
        async def handle(event):
            self.events.append(event);event._gateway_accepted=True;entered.set()
        async def send(chat,text,**kwargs):
            responses.append((chat,text));sent.set();return SimpleNamespace(success=True)
        self.adapter.handle_message=handle;self.adapter.send=send
        self.adapter.ingest(self.event(1))
        await asyncio.wait_for(entered.wait(),2)
        with patch('qqhermes.activity._scope',return_value=(activity._key(1),'snowluma:1')):
            activity.tool_started('terminal',{'command':'agent-browser click @e5'},session_id='s1',tool_call_id='c1')
        self.adapter.ingest(self.event(2))
        status=self.event(3);status['message'][-1]['data']['text']='#status'
        self.adapter.ingest(status)
        await asyncio.wait_for(sent.wait(),2)
        self.assertEqual(len(self.events),1)
        self.assertIn('浏览器点击',responses[-1][1]);self.assertIn('排队 1 条',responses[-1][1])
        self.assertEqual(self.adapter.queues[1].qsize(),1)
        sent.clear()
        status=self.event(4,group=2);status['message'][-1]['data']['text']='#status'
        self.adapter.ingest(status)
        await asyncio.wait_for(sent.wait(),2)
        self.assertIn('没有正在处理',responses[-1][1]);self.assertNotIn('浏览器点击',responses[-1][1])
        await self.adapter.on_processing_complete(self.events[0],SimpleNamespace(value='success'))
        await asyncio.sleep(.1)
        self.assertEqual(len(self.events),2)
        self.assertEqual(activity.snapshot(1)['mid'],'snowluma:2')
        self.assertEqual(len(activity.snapshot(1)['steps']),0)

    async def test_urls_and_old_status_go_to_normal_agent_flow(self):
        for mid,text in enumerate(('https://xxx.com/status','https://xxx.com/#status','/status'),1):
            event=self.event(mid);event['message'][-1]['data']['text']=text
            self.adapter.ingest(event)
        await asyncio.wait_for(self.adapter.queues[1].join(),3)
        self.assertEqual(len(self.events),3)

    async def test_stop_cancels_exact_current_request_and_preserves_fifo(self):
        from types import SimpleNamespace
        entered=asyncio.Event();sent=asyncio.Event();responses=[]
        async def handle(event):
            self.events.append(event);event._gateway_accepted=True;entered.set()
        async def send(chat,text,**kwargs):
            responses.append(text);sent.set();return SimpleNamespace(success=True)
        self.adapter.handle_message=handle;self.adapter.send=send
        self.adapter.interrupt_foreground=AsyncMock()
        self.adapter.ingest(self.event(1))
        await asyncio.wait_for(entered.wait(),2)
        original=self.adapter.foreground[1]
        self.adapter.ingest(self.event(2))
        stop=self.event(3);stop['message'][-1]['data']['text']='#stop'
        self.adapter.ingest(stop)
        await asyncio.wait_for(sent.wait(),2)
        await asyncio.sleep(.1)
        self.assertIn('已中断',responses[0]);self.assertIn('后续 1 条',responses[0])
        self.adapter.interrupt_foreground.assert_awaited_once_with(original)
        self.assertEqual([e.message_id for e in self.events],['snowluma:1','snowluma:2'])
        with self.adapter.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM triggers WHERE message_id=?',('snowluma:1',)).fetchone()[0],'cancelled')
        # A delayed duplicate command pinned to the old target cannot cancel its successor.
        await self.adapter.stop_reply(1,'snowluma:4',original)
        self.assertEqual(self.adapter.interrupt_foreground.await_count,1)
        self.assertEqual(self.adapter.foreground[1]['mid'],'snowluma:2')
        await self.adapter.on_processing_complete(self.events[-1],SimpleNamespace(value='success'))
        await asyncio.wait_for(self.adapter.queues[1].join(),2)
        self.assertNotIn(1,self.adapter.foreground)

    async def test_stop_during_attachment_preparation_and_idle_other_group(self):
        from types import SimpleNamespace
        entered=asyncio.Event()
        self.adapter.send=AsyncMock(return_value=SimpleNamespace(success=True))
        async def blocked(*args):entered.set();await asyncio.Event().wait()
        self.adapter.images.cache_message=blocked
        segments=[{'type':'at','data':{'qq':'99'}},{'type':'image','data':{'url':'https://fixture/image.png'}}]
        self.adapter.ingest(self.event(1,segments=segments))
        await asyncio.wait_for(entered.wait(),2)
        target=self.adapter.foreground[1]
        self.assertIsNone(target['event'])
        await self.adapter.stop_reply(2,'other-group',self.adapter.foreground.get(2))
        self.assertFalse(target['task'].done())
        await self.adapter.stop_reply(1,'stop-preparing',target)
        await asyncio.wait_for(self.adapter.queues[1].join(),2)
        self.assertTrue(target['task'].cancelled());self.assertFalse(self.events)

    async def test_stop_failure_does_not_claim_success_or_kill_successor(self):
        from types import SimpleNamespace
        entered=asyncio.Event()
        async def handle(event):self.events.append(event);event._gateway_accepted=True;entered.set()
        self.adapter.handle_message=handle
        self.adapter.send=AsyncMock(return_value=SimpleNamespace(success=True))
        self.adapter.interrupt_foreground=AsyncMock(side_effect=RuntimeError('test stop failure'))
        self.adapter.ingest(self.event(1))
        await asyncio.wait_for(entered.wait(),2)
        target=self.adapter.foreground[1]
        await self.adapter.stop_reply(1,'stop-failed',target)
        self.assertFalse(target['task'].done())
        self.assertIn('未能确认',self.adapter.send.call_args.args[1])
        await self.adapter.on_processing_complete(self.events[-1],SimpleNamespace(value='success'))
        await asyncio.wait_for(self.adapter.queues[1].join(),2)

    async def test_output_cannot_read_other_group_or_secret(self):
        p=self.root/'state/groups/2/workspace/file';p.parent.mkdir(parents=True);p.write_text('test')
        with self.assertRaises(PermissionError):self.adapter.valid_output(p,1)
        self.assertEqual(self.adapter.valid_output(p,2),p)

    async def test_upload_success_without_message_id_and_explicit_rate_limit(self):
        from types import SimpleNamespace
        p=self.root/'state/groups/1/workspace/test.stl';p.parent.mkdir(parents=True);p.write_bytes(b'fixture')
        request=SimpleNamespace(headers={'Authorization':'Bearer test-token'},
                                json=AsyncMock(return_value={'group':1,'kind':'file','path':str(p)}))
        for _ in range(3):
            response=await self.adapter.send_request(request)
            value=json.loads(response.text)
            self.assertEqual(value,{'success':True,'kind':'file','delivery':'uploaded'})
            self.assertFalse(self.adapter.blobs)
        response=await self.adapter.send_request(request)
        self.assertEqual(response.status,429)
        value=json.loads(response.text)
        self.assertEqual(value['error_kind'],'rate_limited')
        self.assertEqual(value['delivery'],'not_attempted')
        self.assertGreater(value['retry_after'],0)
        self.assertEqual(self.adapter.api.await_count,3)

    async def test_upload_timeout_reports_unknown_without_retry(self):
        p=self.root/'state/groups/1/workspace/test.stl';p.parent.mkdir(parents=True);p.write_bytes(b'fixture')
        self.adapter.api.side_effect=TimeoutError()
        result=await self.adapter.send_document('1',str(p))
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind,'delivery_unknown')
        self.assertFalse(result.retryable)
        self.adapter.api.assert_awaited_once()
        self.assertFalse(self.adapter.blobs)

    async def test_context_deduplicates_but_never_reads_future_messages(self):
        self.adapter.ingest(self.event(10,at=False))
        self.adapter.ingest(self.event(11))
        await self.adapter.queues[1].join()
        self.adapter.ingest(self.event(12))
        self.adapter.ingest(self.event(13,at=False))
        await self.adapter.queues[1].join()
        payload=json.loads(self.events[1].channel_context.split('\n',1)[1])
        self.assertEqual(payload['recent_new_messages'],[])
        self.assertNotIn('snowluma:13',self.events[1].channel_context)
        self.adapter.ingest(self.event(14))
        await self.adapter.queues[1].join()
        self.assertIn('snowluma:13',self.events[2].channel_context)

    async def test_unrelated_turn_does_not_reattach_image_but_explicit_reference_does(self):
        image=self.event(20,at=False,segments=[{'type':'image','data':{'url':'https://qpic.cn/test'}}])
        self.adapter.ingest(image)
        path=self.root/'picture.png';path.write_bytes(b'fixture')
        self.adapter.archive(1).image_result('snowluma:20',0,'ready',path)
        self.adapter.images.cache_message=AsyncMock()
        self.adapter.ingest(self.event(21));await self.adapter.queues[1].join()
        self.assertFalse(self.events[-1].media_urls)
        self.adapter.ingest(self.event(22));await self.adapter.queues[1].join()
        self.assertFalse(self.events[-1].media_urls)
        event=self.event(23);event['message'][-1]['data']['text']='看看刚才那张图'
        self.adapter.ingest(event);await self.adapter.queues[1].join()
        self.assertEqual(self.events[-1].media_urls,[str(path)])

    async def test_other_sender_older_image_is_not_attached_for_generic_question(self):
        image=self.event(24,at=False,segments=[{'type':'image','data':{'url':'https://qpic.cn/test'}}])
        image.update(user_id=43,time=time.time()-3*3600)
        self.adapter.ingest(image)
        path=self.root/'sports.png';path.write_bytes(b'fixture')
        self.adapter.archive(1).image_result('snowluma:24',0,'ready',path)
        self.adapter.images.cache_message=AsyncMock()
        for mid in range(25,35):self.adapter.ingest(self.event(mid,at=False))
        event=self.event(35);event['message'][-1]['data']['text']='如何评价雪狼的运动记录'
        self.adapter.ingest(event);await self.adapter.queues[1].join()
        self.assertEqual(self.events[-1].media_urls,[])
        self.assertNotIn('snowluma:24',self.events[-1].channel_context)

    async def test_image_retrieval_is_authenticated_and_conversation_scoped(self):
        from aiohttp.test_utils import make_mocked_request
        from aiohttp import web
        from PIL import Image
        self.adapter.ingest(self.event(36,at=False,segments=[{'type':'image','data':{'url':'https://qpic.cn/test'}}]))
        archive=self.adapter.archive(1)
        path=archive.path.parent/'media'/'sports.png';path.parent.mkdir();Image.new('RGB',(20,30)).save(path)
        archive.image_result('snowluma:36',0,'ready',path)
        self.adapter.images.cache_message=AsyncMock()
        req=make_mocked_request('POST','/internal/image',headers={'Authorization':'Bearer test-token'})
        req.json=AsyncMock(return_value={'chat_id':'1','message_id':'snowluma:36'})
        result=await self.adapter.image_request(req)
        payload=json.loads(result.text)
        self.assertTrue(payload['images'][0]['image_paths'])
        req.json=AsyncMock(return_value={'chat_id':'2','message_id':'snowluma:36'})
        self.assertEqual((await self.adapter.image_request(req)).status,404)
        req.json=AsyncMock(return_value={'chat_id':'999','message_id':'snowluma:36'})
        with self.assertRaises(web.HTTPForbidden):await self.adapter.image_request(req)
        req=make_mocked_request('POST','/internal/image')
        with self.assertRaises(web.HTTPUnauthorized):await self.adapter.image_request(req)

    async def test_old_quoted_image_is_available_without_recent_snapshot(self):
        old=self.event(30,at=False,segments=[{'type':'image','data':{'url':'https://qpic.cn/test'}}])
        old['time']-=3600;self.adapter.ingest(old)
        path=self.root/'quoted.png';path.write_bytes(b'fixture')
        self.adapter.archive(1).image_result('snowluma:30',0,'ready',path)
        for i in range(31,65):self.adapter.ingest(self.event(i,at=False))
        self.adapter.images.cache_message=AsyncMock()
        event=self.event(65);event['message'].insert(0,{'type':'reply','data':{'id':'snowluma:30'}})
        self.adapter.ingest(event);await self.adapter.queues[1].join()
        self.assertIn(str(path),self.events[-1].media_urls)

    async def test_chat_context_is_bounded(self):
        for i in range(70,100):
            event=self.event(i,at=False);event['message'][-1]['data']['text']='long text '*1000
            self.adapter.ingest(event)
        self.adapter.ingest(self.event(100));await self.adapter.queues[1].join()
        self.assertLess(len(self.events[-1].channel_context),5000)

    async def test_current_file_metadata_survives_compact_context(self):
        event=self.event(110)
        event['message'].append({'type':'file','data':{'name':'report.pdf','file_id':'file-1','file_size':123}})
        self.adapter.ingest(event);await self.adapter.queues[1].join()
        payload=json.loads(self.events[-1].channel_context.split('\n',1)[1])
        self.assertEqual(payload['current_message_id'],'snowluma:110')
        self.assertEqual(payload['current_attachments']['files'][0]['name'],'report.pdf')
        self.assertIn('position',payload['current_attachments']['files'][0])

    async def test_current_and_cached_recent_voice_transcripts_remain_readable(self):
        archive=self.adapter.archive(1)
        async def transcribe(archive,mid):archive.set_transcription(mid,text='transcript '+mid,status='ready')
        self.adapter.transcribe=AsyncMock(side_effect=transcribe)
        voice={'type':'record','data':{'file':'voice.amr'}}
        self.adapter.ingest(self.event(120,at=False,segments=[voice]))
        archive.set_transcription('snowluma:120', text='transcript snowluma:120', status='ready')
        event=self.event(121);event['message'].append(voice)
        self.adapter.ingest(event);await self.adapter.queues[1].join()
        payload=json.loads(self.events[-1].channel_context.split('\n',1)[1])
        self.assertEqual(payload['current_transcription']['text'],'transcript snowluma:121')
        self.assertIn('transcript snowluma:120',payload['recent_new_messages'][0]['text'])
        self.assertEqual(self.adapter.transcribe.await_count, 1)

    def dm(self, mid, user=42, text=None):
        event=self.event(mid,at=False)
        event.pop('group_id')
        event.update(message_type='private',sub_type='friend',user_id=user)
        if text is not None:event['message']=[{'type':'text','data':{'text':text}}]
        return event

    async def test_private_allowlist_no_mention_and_namespace_collision(self):
        self.adapter.ingest(self.dm(1,user=999))
        temporary=self.dm(2);temporary['sub_type']='group';self.adapter.ingest(temporary)
        self.assertFalse(self.adapter.archives)
        self.adapter.ingest(self.dm(3,user=1))
        self.adapter.ingest(self.event(3,group=1))
        await asyncio.gather(self.adapter.queues['dm-1'].join(),self.adapter.queues[1].join())
        self.assertEqual({(e.source.chat_id,e.source.chat_type) for e in self.events},{('dm-1','dm'),('1','group')})
        self.assertNotEqual(self.adapter.archive(1).path,self.adapter.archive('dm-1').path)
        self.adapter.ingest(self.dm(3,user=1))
        self.assertEqual(self.adapter.queues['dm-1'].qsize(),0)

    async def test_private_queue_is_ordered_and_independent(self):
        async def hold(event):self.events.append(event);event._gateway_accepted=True
        self.adapter.handle_message=hold
        self.adapter.ingest(self.dm(1));self.adapter.ingest(self.dm(2))
        self.adapter.ingest(self.dm(3,user=43));self.adapter.ingest(self.event(4))
        await asyncio.sleep(.1)
        self.assertEqual({e.text for e in self.events},{'say 1','say 3','say 4'})
        from types import SimpleNamespace
        await self.adapter.on_processing_complete(next(e for e in self.events if e.text=='say 1'),SimpleNamespace(value='success'))
        await asyncio.sleep(.1)
        self.assertEqual([e.text for e in self.events if e.source.chat_id=='dm-42'],['say 1','say 2'])

    async def test_private_status_bypasses_model(self):
        self.adapter.send=AsyncMock()
        self.adapter.ingest(self.dm(1,text='please #status'))
        await asyncio.gather(*list(self.adapter.tasks))
        self.assertFalse(self.events)
        self.assertEqual(self.adapter.send.call_args.args[0],'dm-42')

    async def test_private_sends_files_and_images_use_private_recipient(self):
        from PIL import Image
        self.adapter.api.return_value={'message_id':'snowluma:200'}
        result=await self.adapter.send('dm-42','hello',reply_to='snowluma:1')
        self.assertTrue(result.success)
        self.assertEqual(self.adapter.api.call_args.args[0],'send_private_msg')
        self.assertEqual(self.adapter.api.call_args.args[1]['user_id'],42)
        self.assertEqual(self.adapter.archive('dm-42').recent()[0]['text'],'[reply to message snowluma:1]hello')
        path=self.root/'state/users/42/workspace/test.png';path.parent.mkdir(parents=True)
        Image.new('RGB',(2,2),'red').save(path)
        self.assertTrue((await self.adapter.send_image_file('dm-42',str(path))).success)
        self.assertEqual(self.adapter.api.call_args.args[0],'send_private_msg')
        self.assertTrue((await self.adapter.send_document('dm-42',str(path))).success)
        self.assertEqual(self.adapter.api.call_args.args[0],'upload_private_file')
        self.assertEqual(self.adapter.api.call_args.args[1]['user_id'],42)
        for destination in ('dm-43','1'):
            with self.assertRaises(PermissionError):self.adapter.valid_output(path,destination)
        self.assertFalse((await self.adapter.send('dm-999','denied')).success)

    async def test_private_image_refresh_validates_sender_and_scope(self):
        import io
        from PIL import Image
        raw=io.BytesIO();Image.new('RGB',(2,2),'red').save(raw,format='PNG')
        e=self.dm(9);e['message']=[{'type':'image','data':{'file':'cached'}}]
        archive=self.adapter.archive('dm-42');archive.save(e)
        fresh={**e,'user_id':43,'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture'}}]}
        self.adapter.api.return_value=fresh
        self.adapter.images.api=self.adapter.api
        with patch('qqhermes.media.download_image',AsyncMock(return_value=raw.getvalue())) as download:
            await self.adapter.images.cache_message(archive,'dm-42','snowluma:9')
            download.assert_not_called()
            self.assertEqual(archive.images('snowluma:9')[0]['status'],'unavailable')
            fresh['user_id']=42
            await self.adapter.images.cache_message(archive,'dm-42','snowluma:9')
            self.assertEqual(archive.images('snowluma:9')[0]['status'],'ready')

    async def test_private_file_lookup_uses_recorded_hash_and_owner(self):
        from qqhermes.files import fetch_attachment
        e=self.dm(10);e['message']=[{'type':'file','data':{'file_id':'fid','file_hash':'hash','name':'test.txt'}}]
        archive=self.adapter.archive('dm-42');archive.save(e)
        self.adapter.api.return_value={'url':'http://127.0.0.1/blocked'}
        with self.assertRaises(ValueError):await fetch_attachment(self.adapter.api,archive,'dm-42','snowluma:10',0)
        self.adapter.api.assert_awaited_once_with('get_private_file_url',{'user_id':42,'file_id':'fid','file_hash':'hash'})


if __name__=='__main__':unittest.main()
