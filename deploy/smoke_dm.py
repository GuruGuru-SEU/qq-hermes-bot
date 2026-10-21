"""Real-model private routing and timed cron delivery. ISOLATED STAGING ONLY."""
import asyncio
import json
import os
from pathlib import Path
import time
import aiohttp


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    first,second=cfg['trusted_users'][:2]
    group=cfg['trusted_groups'][0]
    replies=[]
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);result={}
                    action=request['action'];params=request['params']
                    if action in ('send_private_msg','send_group_msg'):
                        text=''.join(s['data'].get('text','') for s in params['message'])
                        recipient=params.get('user_id',params.get('group_id'))
                        replies.append((action,recipient,text))
                        print('STAGING_REPLY',action,recipient,text[:300],flush=True)
                        result={'message_id':920000+len(replies)}
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            task=asyncio.create_task(reader())
            async def emit(mid,user,text,private=True):
                event={'post_type':'message','message_type':'private' if private else 'group',
                       'sub_type':'friend' if private else 'normal','user_id':user,'message_id':mid,
                       'time':time.time(),'sender':{'nickname':'Synthetic test'},
                       'message':[{'type':'text','data':{'text':text}}]}
                if not private:
                    event['group_id']=group
                    event['message'].insert(0,{'type':'at','data':{'qq':str(cfg['bot_qq'])}})
                await ws.send_json(event)
            async def until(predicate,timeout=240):
                end=time.monotonic()+timeout
                while not predicate():
                    if time.monotonic()>end:raise TimeoutError('DM staging test timed out')
                    await asyncio.sleep(.5)
            def got(action,recipient,marker):
                return any(a==action and r==recipient and marker in text for a,r,text in replies)
            await emit(300,first,'Use terminal to calculate 27*31. Then query qq_history action recent to verify this message is in my private history. Reply only DM_READY 837 after both tools succeed.')
            await emit(301,second,'Reply exactly SECOND_DM_READY.')
            await emit(302,first,'Reply exactly GROUP_READY.',private=False)
            await until(lambda:got('send_private_msg',first,'DM_READY 837') and got('send_private_msg',second,'SECOND_DM_READY') and got('send_group_msg',group,'GROUP_READY'))
            await emit(303,first,'Create a one-time native cronjob scheduled 1m from now, repeat=1, deliver=origin. Its only task prompt must be: Reply exactly DM_CRON_OK. Do not send DM_CRON_OK now. After the cronjob tool confirms creation, reply DM_SCHEDULED.')
            await until(lambda:got('send_private_msg',first,'DM_SCHEDULED'))
            await until(lambda:got('send_private_msg',first,'DM_CRON_OK'),timeout=240)
            assert not any('DM_CRON_OK' in text and (a!='send_private_msg' or r!=first) for a,r,text in replies)
            print('PRIVATE ROUTING, TOOLS, CONCURRENT GROUP AND TIMED CRON PASSED',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)


asyncio.run(main())
