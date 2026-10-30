"""Real-model mentions through a fake OneBot gateway; never send to real QQ."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time

import aiohttp
from PIL import Image


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    assert cfg['trusted_users']==[900003,900004]
    replies=[];checks=[]
    headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers=headers) as ws:
            async def reader():
                async for frame in ws:
                    call=json.loads(frame.data);action=call['action'];params=call['params'];result={}
                    if action=='get_group_member_info':
                        assert params.get('no_cache') is False,params
                        checks.append((params['group_id'],params['user_id']))
                        if params['group_id'] in (900001,900002) and params['user_id'] in (1001,1002):
                            result={'group_id':params['group_id'],'user_id':params['user_id'],'nickname':'Alice' if params['user_id']==1001 else 'Bob'}
                    elif action in ('send_group_msg','send_private_msg'):
                        segments=params['message']
                        replies.append({'action':action,'chat':params.get('group_id',params.get('user_id')),
                            'mentions':[s['data']['qq'] for s in segments if s['type']=='at'],
                            'text':''.join(s['data'].get('text','') for s in segments if s['type']=='text'),
                            'images':sum(s['type']=='image' for s in segments)})
                        print(json.dumps(replies[-1],ensure_ascii=False),flush=True)
                        result={'message_id':990000+len(replies)}
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':call['echo']})
            task=asyncio.create_task(reader())
            try:
                async def endpoint(body):
                    async with client.post('http://127.0.0.1:8080/internal/send',headers=headers,json=body) as response:
                        return await response.json()

                # Exercise the exact internal HTTP path used by scheduled agents.
                result=await endpoint({'chat_id':'900002','kind':'text','text':'Endpoint test','mention_user_ids':['1002']})
                assert result['success'] and result['mention_user_ids']==['1002'],result
                path=Path('/state/groups/900002/workspace/mention.png')
                path.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(8,8),'red').save(path)
                result=await endpoint({'chat_id':'900002','kind':'image','path':str(path),'text':'Image test','mention_user_ids':['1001']})
                assert result['success'],result
                assert replies[-1]['images']==1 and replies[-1]['mentions']==['1001']
                result=await endpoint({'chat_id':'900002','kind':'text','text':'must not send','mention_user_ids':['999999']})
                assert not result['success'] and result['delivery']=='not_attempted',result
                result=await endpoint({'chat_id':'dm-900003','kind':'text','text':'must not send','mention_user_ids':['1001']})
                assert not result['success'] and result['delivery']=='not_attempted',result
                assert len(replies)==2,replies
                print('PASS HTTP text/image mentions; invalid targets and DM rejected without sends',flush=True)

                async def send(mid,text,targets=()):
                    await ws.send_json({'post_type':'message','message_type':'group','group_id':900001,
                        'message_id':mid,'user_id':1001,'time':time.time(),'sender':{'nickname':'Alice'},
                        'message':[{'type':'at','data':{'qq':str(u)}} for u in (99,*targets)]+[{'type':'text','data':{'text':text}}]})

                def done(mid):
                    with sqlite3.connect('/state/transport.sqlite3') as db:
                        row=db.execute('SELECT status FROM triggers WHERE message_id=?',('snowluma:'+str(mid),)).fetchone()
                    return row and row[0] not in ('queued','processing','accepted')

                async def await_done(mid):
                    deadline=time.monotonic()+240
                    while not done(mid):
                        if task.done():await task
                        if time.monotonic()>deadline:raise TimeoutError('Mention smoke timed out')
                        await asyncio.sleep(.25)
                    await asyncio.sleep(1)

                start=len(replies)
                await send(8101,'请真正@刚才一起提到的那位成员（不是机器人），告诉他：测试完成。只发送一条消息。',targets=(1002,))
                await await_done(8101)
                sent=replies[start:]
                assert len(sent)==1 and sent[0]['mentions']==['1002'] and '测试完成' in sent[0]['text'],sent
                print('PASS model resolves incoming @ target and delivers one mentioned result',flush=True)

                start=len(replies)
                await send(8102,'请用工具算出19乘23，算好以后真正@我，告诉我结果。只发一条结果消息。')
                await await_done(8102)
                sent=replies[start:]
                assert len(sent)==1 and sent[0]['mentions']==['1001'] and '437' in sent[0]['text'],sent
                assert (900001,1001) in checks and (900001,1002) in checks,checks
                print('PASS model mentions requester; no final recap duplicates; ALL MENTION SMOKES PASSED',flush=True)

                start=len(replies)
                await send(8103,'请先尝试用 qq_send(kind=text, reason=requested) 真正@我，文字为“收到”。如果工具要求改用 qq_work，就按它的要求改用。结果成功发送后输出 [SILENT]，不要再发第二条消息。')
                await await_done(8103)
                sent=replies[start:]
                assert len(sent)==1 and sent[0]['mentions']==['1001'] and '收到' in sent[0]['text'],sent
                assert not any('silence marker' in r['text'] for r in sent),sent
                with sqlite3.connect('/state/transport.sqlite3') as db:
                    report=db.execute("SELECT status FROM work_reports WHERE root_mid='snowluma:8103' AND kind='result'").fetchone()
                assert report==('sent',),report
                print('PASS qq_send foreground recovery uses confirmed task result; no silence warning',flush=True)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)


asyncio.run(main())
