"""Real Hermes vision and consent/delegation test. ISOLATED STAGING ONLY."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time

import aiohttp
from PIL import Image
from qqhermes.history import History


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    group=cfg['trusted_groups'][0]
    state=Path(os.environ['QQ_STATE'])
    replies=[]
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);result={}
                    if request.get('action')=='send_group_msg':
                        text=''.join(s['data'].get('text','') for s in request['params']['message'])
                        replies.append(text);print('REPLY',text,flush=True)
                        result={'message_id':910000+len(replies)}
                    elif request.get('action')=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            task=asyncio.create_task(reader())
            async def emit(mid,text):
                await ws.send_json({'post_type':'message','message_type':'group','group_id':group,'user_id':123456,
                    'message_id':mid,'time':time.time(),'sender':{'nickname':'Staging test'},'message':[
                    {'type':'at','data':{'qq':str(cfg['bot_qq'])}},{'type':'text','data':{'text':text}}]})
            async def until(predicate,timeout=180):
                end=time.monotonic()+timeout
                while not predicate():
                    if time.monotonic()>end:raise TimeoutError('Staging capability test timed out')
                    await asyncio.sleep(.5)
            archive=History(state/'groups'/str(group)/'history.sqlite3')
            image=state/'groups'/str(group)/'media/synthetic-red.png'
            image.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(64,64),(255,0,0)).save(image)
            archive.save({'message_id':'snowluma:200','user_id':123456,'time':time.time(),'sender':{'nickname':'Staging test'},
                'message':[{'type':'image','data':{'url':'https://qpic.cn/synthetic-red'}}]})
            archive.image_result('snowluma:200',0,'ready',image)
            await emit('201','Use the attached most recent image. What single English word describes its color? Reply only that word.')
            await until(lambda:bool(replies))
            assert 'red' in replies[-1].lower(),replies
            n=len(replies)
            await emit('202','Please run a background agent whose only goal is to calculate 27*31 using terminal and report BACKGROUND_OK with the result. Use delegate_task. Ask me to approve before starting.')
            await until(lambda:len(replies)>n)
            db=sqlite3.connect(state/'transport.sqlite3')
            row=db.execute("SELECT id FROM proposals WHERE status='pending' ORDER BY created DESC LIMIT 1").fetchone()
            assert row,'No native delegation consent proposal was recorded'
            assert db.execute('SELECT count(*) FROM children').fetchone()[0]==0,'Child started before approval'
            await emit('203','/approve '+row[0])
            await until(lambda:db.execute('SELECT count(*) FROM children').fetchone()[0]>0)
            await until(lambda:any('BACKGROUND_OK' in text and '837' in text for text in replies[n:]),timeout=240)
            print('STAGING VISION AND BACKGROUND CONSENT PASSED',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)


asyncio.run(main())
