import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from qqhermes.plaintext import plain_reply

TEXT = '周末去公园走走挺好的，最近天气适合散步。回来可以顺便买点水果，再做一顿简单的晚饭。晚上留点时间看电影，挑一部轻松的就行。'
PARTS = ['周末去公园走走挺好的，最近天气适合散步。','回来可以顺便买点水果，再做一顿简单的晚饭。','晚上留点时间看电影，挑一部轻松的就行。']


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)
        cfg=self.path/'config.json';cfg.write_text(json.dumps({'bot_qq':99,'trusted_groups':[1,2],'trusted_users':[42],
            'reply_bubbles':{'enabled':True,'max_messages':3}}))
        self.env=patch.dict(os.environ,{'QQ_CONFIG':str(cfg),'QQ_STATE':str(self.path/'state'),'ONEBOT_TOKEN':'test'})
        self.env.start()
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry
        from gateway.platform_registry import platform_registry
        from qqhermes.adapter import QQAdapter
        platform_registry.register(PlatformEntry(name='qq_onebot',label='QQ test',adapter_factory=QQAdapter,check_fn=lambda:True))
        self.adapter=QQAdapter(PlatformConfig(enabled=True))
        self.calls=[]
        async def api(action,args,**kw):
            self.calls.append((action,args))
            return {'message_id':f'out-{len(self.calls)}'}
        self.adapter.api=api

    async def asyncTearDown(self):
        self.env.stop();self.tmp.cleanup()

    def metadata(self,identity='final-1'):
        return {'qq_final_delivery':{'id':identity,'content':TEXT}}

    async def test_each_part_archived_first_quoted_receipts_and_idempotency(self):
        result=await self.adapter.send('1',TEXT,reply_to='in-1',metadata=self.metadata())
        self.assertTrue(result.success)
        self.assertEqual(result.message_id,'out-1')
        self.assertEqual(result.continuation_message_ids,())
        self.assertEqual(''.join(c[1]['message'][-1]['data']['text'] for c in self.calls),TEXT)
        self.assertEqual([len(c[1]['message']) for c in self.calls],[2])
        self.assertEqual(len(self.adapter.store.outgoing('final-1')['receipts']),1)
        from qqhermes.history import query
        self.assertEqual(query(self.adapter.archive(1).path)['total'],1)
        # Native recovery passes a visible prefix; the stable identity/raw answer avoids duplication.
        again=await self.adapter.send('1','Recovered: '+TEXT,metadata=self.metadata())
        self.assertTrue(again.success);self.assertEqual(len(self.calls),1)
        await self.adapter.send('1',TEXT,metadata=self.metadata('different-user-turn'))
        self.assertEqual(len(self.calls),2)

    async def test_uncertain_send_is_not_replayed(self):
        self.adapter.api=AsyncMock(side_effect=TimeoutError('lost acknowledgement'))
        result=await self.adapter.send('1',TEXT,metadata=self.metadata())
        self.assertFalse(result.success);self.assertFalse(result.retryable)
        self.assertEqual(result.raw_response['message_ids'],[])
        self.assertEqual([r['status'] for r in self.adapter.store.outgoing('final-1')['receipts']],['attempting'])
        result=await self.adapter.send('1',TEXT,metadata=self.metadata())
        self.assertFalse(result.success);self.adapter.api.assert_awaited_once()

    async def test_restart_resumes_only_known_unsent_tail(self):
        row=self.adapter.store.prepare_outgoing('final-1',1,TEXT,'in-1',PARTS)
        self.adapter.store.outgoing_status('final-1',0,'sent','previous-ack')
        from qqhermes.store import Store
        self.adapter.store=Store(self.adapter.store.path)  # disk reopen
        result=await self.adapter.send('1',TEXT,metadata=self.metadata())
        self.assertTrue(result.success)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0][1]['message'][-1]['data']['text'],''.join(PARTS[1:]))
        self.assertEqual(result.continuation_message_ids,('previous-ack',))
        self.assertTrue(all(len(args['message'])==1 for _,args in self.calls))

    async def test_cancellation_in_send_is_ambiguous_after_restart(self):
        entered=asyncio.Event()
        async def blocked(*args,**kwargs):
            entered.set();await asyncio.Event().wait()
        self.adapter.api=blocked
        task=asyncio.create_task(self.adapter.send('1',TEXT,metadata=self.metadata()))
        await asyncio.wait_for(entered.wait(),2);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.adapter.api=AsyncMock()
        result=await self.adapter.send('1',TEXT,metadata=self.metadata())
        self.assertFalse(result.success);self.adapter.api.assert_not_awaited()

    async def test_same_chat_progress_and_media_wait_other_chat_independent(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.adapter.api
        async def blocked(action,args,**kw):
            if args.get('group_id')==1 and not entered.is_set():
                entered.set();await release.wait()
            return await original(action,args,**kw)
        self.adapter.api=blocked
        task=asyncio.create_task(self.adapter.send('1',TEXT,metadata=self.metadata()))
        await asyncio.wait_for(entered.wait(),2)
        progress=asyncio.create_task(self.adapter.send('1','progress'))
        self.adapter._send_image_file=AsyncMock(return_value='image-result')
        self.adapter._send_document=AsyncMock(return_value='file-result')
        image=asyncio.create_task(self.adapter.send_image_file('1','image.png'))
        document=asyncio.create_task(self.adapter.send_document('1','file.pdf'))
        independent=await asyncio.wait_for(self.adapter.send('2','independent'),2)
        self.assertTrue(independent.success);self.assertFalse(progress.done())
        self.adapter._send_image_file.assert_not_awaited();self.adapter._send_document.assert_not_awaited()
        release.set();await asyncio.gather(task,progress,image,document)
        group1=[a['message'][-1]['data']['text'] for _,a in self.calls if a.get('group_id')==1]
        self.assertEqual(group1,[TEXT,'progress'])
        self.adapter._send_image_file.assert_awaited_once()
        self.adapter._send_document.assert_awaited_once()

    async def test_native_recovery_passes_stable_identity_and_does_not_duplicate(self):
        from gateway.run_startup import GatewayStartupMixin
        from types import SimpleNamespace
        from unittest.mock import Mock
        await self.adapter.send('1',TEXT,metadata=self.metadata())
        runner=SimpleNamespace(_obligation_adapter=AsyncMock(return_value=self.adapter))
        row={'chat_id':'1','platform':'qq_onebot','obligation_id':'final-1','content':TEXT,
             'needs_marker':True,'attempts':1}
        with patch('gateway.delivery_ledger.mark_delivered',new=Mock()) as marked:
            count=await GatewayStartupMixin._redeliver_claimed_obligations(runner,[row])
        self.assertEqual(count,1);marked.assert_called_once_with('final-1')
        self.assertEqual(len(self.calls),1)

    async def test_native_final_hook_marks_only_normal_answers(self):
        from gateway.platforms.base import BasePlatformAdapter
        from types import SimpleNamespace
        event=SimpleNamespace(text='hello',message_id='in-1')
        with patch.object(BasePlatformAdapter,'send_final_ledgered',new_callable=AsyncMock) as native:
            await self.adapter.send_final_ledgered(event,'session',TEXT,{},reply_to='in-1')
            self.assertEqual(native.call_args.args[3]['qq_final_delivery']['content'],TEXT)
            event.text='/status'
            await self.adapter.send_final_ledgered(event,'session',TEXT,{},reply_to='in-1')
            self.assertNotIn('qq_final_delivery',native.call_args.args[3])

    async def test_status_and_dm_and_long_text(self):
        await self.adapter.send('1',TEXT)  # old bubble config cannot enable splitting
        self.calls.clear()
        result=await self.adapter.send('dm-42',TEXT,metadata=self.metadata('dm-final'))
        self.assertTrue(result.success);self.assertTrue(all(action=='send_private_msg' for action,_ in self.calls))
        self.calls.clear()
        text='long text '*650+'END'
        await self.adapter.send('1',text)
        self.assertEqual(''.join(a['message'][-1]['data']['text'] for _,a in self.calls),text)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__': unittest.main()
