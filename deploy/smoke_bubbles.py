"""Real-model bubble test. Only synthetic staging IDs; never sends to QQ."""
import asyncio
import json
import os
from pathlib import Path
import time
import aiohttp

ANSWER = '今天这场雨终于停了，我准备去楼下走走，顺便买点新鲜水果。\n\n对了，你上次推荐的那部电影我还记着，晚上正好找时间看看。\n\n还有件小事，阳台那盆薄荷又长出了新叶，明天可以试着泡杯茶。'


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    sent=[];cleared={};events=[]
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',
                headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);action=request['action'];params=request['params'];result={}
                    if action in ('send_group_msg','send_private_msg'):
                        segments=params['message']
                        item={'chat':params.get('group_id',params.get('user_id')),
                            'quote':next((str(s['data']['id']) for s in segments if s['type']=='reply'),None),
                            'text':''.join(s['data'].get('text','') for s in segments),
                            'at':time.monotonic()}
                        sent.append(item);events.append(('send',item))
                        print(json.dumps(item,ensure_ascii=False),flush=True)
                        result={'message_id':960000+len(sent)}
                    elif action=='set_msg_emoji_like' and not params['set']:
                        cleared[str(params['message_id'])]=time.monotonic()
                        events.append(('clear',str(params['message_id'])))
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            reader_task=asyncio.create_task(reader())
            async def ask(mid,group,text):
                await ws.send_json({'post_type':'message','message_type':'group','group_id':group,
                    'user_id':123456,'message_id':mid,'time':time.time(),'sender':{'nickname':'Synthetic member'},
                    'message':[{'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':text}}]})
            await ask(701,900001,'本次测试不需要任何工具。请只原样回复下面三个自然段，不加开头结尾：\n'+ANSWER)
            await ask(702,900001,'本次只回复“第二条已收到”，不要做其他事。')
            await ask(703,900002,'本次只回复“另一个群正常”，不要做其他事。')
            deadline=time.monotonic()+180
            while not all(mid in cleared for mid in ('701','702','703')):
                if reader_task.done():await reader_task
                if time.monotonic()>deadline:raise TimeoutError('staging turn did not complete')
                await asyncio.sleep(.25)
            group=[s for s in sent if s['chat']==900001]
            assert group[0]['quote']=='701',group
            first=group[:-1]
            assert 2<=len(first)<=3,group
            assert all(s['quote'] is None for s in first[1:]),group
            assert group[-1]['quote']=='702' and '第二条已收到' in group[-1]['text'],group
            normalize=lambda s:''.join(s.split())
            assert normalize(''.join(s['text'] for s in first))==normalize(ANSWER),first
            assert first[-1]['at']<=cleared['701']<=group[-1]['at']
            for a,b in zip(first,first[1:]):
                assert b['at']-a['at']>=.5,(a,b)
            assert any(s['quote']=='703' and '另一个群正常' in s['text'] for s in sent)
            from qqhermes.store import Store
            store=Store(Path(os.environ['QQ_STATE'])/'transport.sqlite3')
            with store.db() as db:
                rows=db.execute("SELECT status,count(*) AS n FROM outbound_parts GROUP BY status").fetchall()
            assert all(r['status']=='sent' for r in rows),[dict(r) for r in rows]
            print('BUBBLE GATEWAY CHECKS PASSED: lossless real-model split, first-only quote, FIFO, pacing, receipt cleanup, durable ACKs',flush=True)
            reader_task.cancel();await asyncio.gather(reader_task,return_exceptions=True)

asyncio.run(main())
