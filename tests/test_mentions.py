import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image
import test_integration as fixtures
from qqhermes.conversation import Conversation
from qqhermes.context import ContextWindow
from qqhermes.live import LiveInbox, describe
from qqhermes.mentions import normalize_mentions
from qqhermes.store import Store
from qqhermes.tools import send_tool


class MentionValidationTests(unittest.TestCase):
    def test_foreground_text_cannot_bypass_task_receipts(self):
        with patch('qqhermes.tools.conversation_for_profile',return_value=Conversation('group',1)), patch('qqhermes.live.scope',return_value=(Conversation('group',1),'m')), patch('httpx.post') as post:
            result=json.loads(send_tool({'kind':'text','reason':'requested','text':'Ready','mention_user_ids':['42']}))
        self.assertFalse(result['success'])
        self.assertEqual(result['delivery'],'not_attempted')
        self.assertIn('qq_work',result['hint'])
        post.assert_not_called()

    def test_only_explicit_numeric_group_targets_are_allowed(self):
        self.assertEqual(normalize_mentions(1,['42',42,'43']), ['42','43'])
        self.assertEqual(normalize_mentions('dm-42',[]), [])
        for value in ('42', ['all'], ['Alice'], ['0'], [True], [42.0], ['４２'], ['42,all'], list(range(1,7))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_mentions(1,value)
        with self.assertRaises(ValueError):normalize_mentions('dm-42',['42'])

    def test_tool_forwards_mentions_as_structured_data(self):
        response=httpx.Response(200,json={'success':True,'mention_user_ids':['42']})
        with patch('httpx.post',return_value=response) as post, patch('qqhermes.tools.conversation_for_profile',return_value=Conversation('group',1)), patch.dict(os.environ,{'ONEBOT_TOKEN':'fixture'}):
            result=json.loads(send_tool({'kind':'text','reason':'requested','text':'Ready','mention_user_ids':['42']}))
        self.assertEqual(post.call_args.kwargs['json']['mention_user_ids'],['42'])
        self.assertEqual(result['mention_user_ids'],['42'])


class MentionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.AdapterTests.asyncSetUp
    asyncTearDown=fixtures.AdapterTests.asyncTearDown
    event=fixtures.AdapterTests.event

    def prepare_api(self):
        self.sent=[]
        async def api(action,args,**kwargs):
            if action=='get_group_member_info':
                return {'group_id':args['group_id'],'user_id':args['user_id'],'nickname':'Member'}
            self.sent.append((action,args))
            return {'message_id':'snowluma:99'}
        self.adapter.api=AsyncMock(side_effect=api)

    def metadata(self,identity='mention-test',mentions=None):
        result={'qq_final_delivery':{'id':identity,'content':'Ready'}}
        if mentions is not None:result['mention_user_ids']=mentions
        return result

    async def test_one_message_real_at_quote_archive_and_idempotent_recovery(self):
        self.prepare_api()
        metadata=self.metadata(mentions=['43','43'])
        result=await self.adapter.send('1','Ready',reply_to='snowluma:1',metadata=metadata)
        self.assertTrue(result.success)
        action,params=self.sent[0]
        self.assertEqual(action,'send_group_msg')
        self.assertEqual(params['message'],[{'type':'reply','data':{'id':'snowluma:1'}},
            {'type':'at','data':{'qq':'43'}},{'type':'text','data':{'text':' '}},
            {'type':'text','data':{'text':'Ready'}}])
        self.assertEqual(self.adapter.archive(1).segments('snowluma:99'),params['message'])
        self.assertEqual(self.adapter.gateway.params(action,params)['message'][1],params['message'][1])
        self.adapter.store=Store(self.adapter.store.path)
        result=await self.adapter.send('1','Recovered: Ready',metadata=self.metadata())
        self.assertTrue(result.success);self.assertEqual(len(self.sent),1)
        changed=await self.adapter.send('1','Ready',metadata=self.metadata(mentions=['42']))
        self.assertFalse(changed.success);self.assertEqual(len(self.sent),1)

    async def test_legacy_plain_text_does_not_create_mentions_and_dm_is_unchanged(self):
        self.prepare_api()
        await self.adapter.send('1','@43 [CQ:at,qq=43]')
        await self.adapter.send('dm-42','Hello')
        self.assertFalse(any(s['type']=='at' for _,p in self.sent for s in p['message']))
        self.assertEqual(self.sent[-1][0],'send_private_msg')
        self.assertFalse(any(c.args[0]=='get_group_member_info' for c in self.adapter.api.call_args_list))

    async def test_invalid_or_nonmember_target_prevents_all_sending(self):
        self.prepare_api()
        for chat,users in [('dm-42',['42']),('1',['all'])]:
            result=await self.adapter.send(chat,'Ready',metadata={'mention_user_ids':users})
            self.assertEqual(result.raw_response['delivery'],'not_attempted')
        self.adapter.api.assert_not_awaited()
        for response in ({}, {'user_id':43,'group_id':2}):
            self.adapter.api=AsyncMock(return_value=response)
            result=await self.adapter.send('1','Ready',metadata={'mention_user_ids':['43']})
            self.assertFalse(result.success)
            self.assertEqual(result.raw_response['delivery'],'not_attempted')
            self.assertTrue(all(c.args[0]=='get_group_member_info' for c in self.adapter.api.call_args_list))
        self.adapter.api=AsyncMock(side_effect=TimeoutError())
        result=await self.adapter.send('1','Ready',metadata={'mention_user_ids':['43']})
        self.assertEqual(result.raw_response['delivery'],'not_attempted')

    async def test_pending_receipt_retains_mentions_and_unknown_send_never_retries(self):
        self.prepare_api()
        self.adapter.store.prepare_outgoing('pending',1,'Ready',None,['Ready'],mentions=['43'])
        self.assertTrue((await self.adapter.send('1','Ready',metadata=self.metadata('pending'))) .success)
        self.assertEqual(self.sent[-1][1]['message'][0],{'type':'at','data':{'qq':'43'}})
        original=self.adapter.api.side_effect
        async def lost(action,args,**kwargs):
            if action=='send_group_msg':raise TimeoutError()
            return await original(action,args,**kwargs)
        self.adapter.api=AsyncMock(side_effect=lost)
        metadata=self.metadata('lost',['43'])
        self.assertFalse((await self.adapter.send('1','Ready',metadata=metadata)).success)
        self.assertFalse((await self.adapter.send('1','Ready',metadata=metadata)).success)
        self.assertEqual(sum(c.args[0]=='send_group_msg' for c in self.adapter.api.call_args_list),1)

    async def test_work_result_mentions_once_and_final_recap_stays_silent(self):
        self.prepare_api();live=LiveInbox(self.adapter.store);data=self.event(1);mid=data['message_id']
        self.adapter.store.enqueue(1,data,[]);self.adapter.store.finish(1,mid,'processing');live.start(1,data)
        args={'chat_id':'1','root_mid':mid,'kind':'result','text':'Ready','mention_user_ids':['43']}
        self.assertTrue((await self.adapter.report_work(args))['success'])
        self.assertTrue((await self.adapter.report_work(args))['success'])
        self.assertFalse((await self.adapter.report_work({**args,'mention_user_ids':['42']}))['success'])
        event=SimpleNamespace(source=SimpleNamespace(chat_id='1'),message_id=mid,text='request')
        result,_=await self.adapter.send_final_ledgered(event,'session','Warning: the model returned only a silence marker',{},reply_to=mid)
        self.assertTrue(result.success);self.assertEqual(len(self.sent),1)
        self.assertTrue(any(s['type']=='at' for s in self.sent[0][1]['message']))

    async def test_followup_during_member_lookup_prevents_stale_result(self):
        self.prepare_api();live=LiveInbox(self.adapter.store);data=self.event(1);mid=data['message_id']
        self.adapter.store.enqueue(1,data,[]);self.adapter.store.finish(1,mid,'processing');live.start(1,data)
        original=self.adapter.api.side_effect
        async def incoming(action,args,**kwargs):
            if action=='get_group_member_info':self.adapter.store.enqueue(1,self.event(2),[])
            return await original(action,args,**kwargs)
        self.adapter.api.side_effect=incoming
        args={'chat_id':'1','root_mid':mid,'kind':'result','text':'Old result','mention_user_ids':['42']}
        result=await self.adapter.report_work(args)
        self.assertEqual(result['delivery'],'not_attempted');self.assertFalse(self.sent)
        live.take(1,mid);live.resolve(1,mid,{'message_id':'snowluma:2','relation':'refine','task_id':mid})
        self.adapter.api.side_effect=original
        result=await self.adapter.report_work({**args,'text':'Updated result'})
        self.assertTrue(result['success']);self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0][1]['message'][-1]['data']['text'],'Updated result')

    async def test_image_and_caption_share_mention_message_and_files_reject(self):
        self.prepare_api()
        path=self.adapter.archive(1).path.parent/'red.png'
        Image.new('RGB',(5,5),'red').save(path)
        result=await self.adapter.send_image_file('1',str(path),'Ready',mention_user_ids=['43'])
        self.assertTrue(result.success)
        self.assertEqual([s['type'] for s in self.sent[0][1]['message']],['at','text','image','text'])
        request=SimpleNamespace(headers={'Authorization':'Bearer test-token'},json=AsyncMock(return_value={
            'chat_id':'1','kind':'file','path':str(path),'mention_user_ids':['43']}))
        response=await self.adapter.send_request(request)
        self.assertEqual(json.loads(response.body)['delivery'],'not_attempted')
        self.assertEqual(len(self.sent),1)

    async def test_incoming_target_ids_available_initial_and_followup(self):
        event=self.event(1,segments=[{'type':'at','data':{'qq':'99'}},{'type':'at','data':{'qq':'43'}},
            {'type':'text','data':{'text':'please notify this member'}}])
        archive=self.adapter.archive(1);archive.save(event)
        payload,_,_=ContextWindow().render(archive,event,[],[],False)
        self.assertEqual(json.loads(payload)['current_mention_user_ids'],['99','43'])
        chat=SimpleNamespace(directory=archive.path.parent)
        self.assertEqual(describe(chat,[event])[0]['mention_user_ids'],['99','43'])
