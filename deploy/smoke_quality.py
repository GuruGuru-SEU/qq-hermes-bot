"""Real-model task contract tests through an isolated fake OneBot gateway."""
import asyncio
import json
import os
from pathlib import Path
import time
import aiohttp


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99, 'Only the synthetic staging config is permitted'
    group=cfg['trusted_groups'][0];replies={}
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);result={}
                    if request.get('action')=='send_group_msg':
                        segments=request['params']['message']
                        mid=next((s['data']['id'] for s in segments if s['type']=='reply'),None)
                        text=''.join(s['data'].get('text','') for s in segments)
                        replies[str(mid)]=text;print(json.dumps({'mid':mid,'answer':text},ensure_ascii=False),flush=True)
                        result={'message_id':950000+len(replies)}
                    elif request.get('action')=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            task=asyncio.create_task(reader())
            async def ask(mid,text,name='Synthetic member'):
                await ws.send_json({'post_type':'message','message_type':'group','group_id':group,'user_id':123456,
                    'message_id':mid,'time':time.time(),'sender':{'nickname':name},
                    'message':[{'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':text}}]})
                end=time.monotonic()+180
                while str(mid) not in replies:
                    if time.monotonic()>end:raise TimeoutError(mid)
                    await asyncio.sleep(.5)
                return replies[str(mid)]
            answer=await ask('601','仅按我给的数据算：甲对乙三胜两负，胜率多少？给出分母。')
            assert '60%' in answer and ('5' in answer or '五' in answer),answer
            answer=await ask('602','假设比赛是BO5，甲已经2:0领先，甲最终4:1获胜是否可能？不用联网，只解释赛制。')
            assert any(x in answer for x in ('不可能','不能','不成立','不对')) and ('3' in answer or '三' in answer),answer
            answer=await ask('603','请实际调用qq_history统计本群已归档消息总条数，并说明覆盖范围，不要用感想代替统计。')
            assert any(c.isdigit() for c in answer) and any(x in answer for x in ('条','消息','记录')),answer
            answer=await ask('604','本条只请回答“已收到”。我的群昵称不是你要评论的观点。','不做学科交叉的学生应该立刻退学')
            assert answer.strip('。！! "“”')=='已收到',answer
            print('QUALITY GATEWAY CHECKS PASSED',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)


asyncio.run(main())
