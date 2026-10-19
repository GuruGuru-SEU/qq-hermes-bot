"""Run INSIDE an isolated staging container, never against a real QQ connection."""
import asyncio,json,os,time
from pathlib import Path
import aiohttp

async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    groups=cfg['trusted_groups']; replies=[];actions=[]
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def read():
                async for frame in ws:
                    data=json.loads(frame.data);action=data.get('action');actions.append(action)
                    result={}
                    if action=='send_group_msg':
                        text=''.join(s['data'].get('text','') for s in data['params']['message'])
                        replies.append({'group':data['params']['group_id'],'text':text,'reply_to':[s['data']['id'] for s in data['params']['message'] if s['type']=='reply']})
                        print(json.dumps(replies[-1],ensure_ascii=False),flush=True)
                        result={'message_id':900000+len(replies)}
                    elif action=='get_group_msg_history': result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':data['echo']})
            reader=asyncio.create_task(read())
            async def emit(mid,group,text):
                await ws.send_json({'post_type':'message','message_type':'group','group_id':group,'user_id':123456,'message_id':mid,'time':time.time(),
                    'sender':{'nickname':'Staging test'},'message':[{'type':'at','data':{'qq':str(cfg['bot_qq'])}},{'type':'text','data':{'text':text}}]})
            await emit('100',groups[0],'Deployment test: use terminal to compute 19*23, use qq_history action stats, then briefly report the answer and archived message count. Do not send extra messages.')
            await emit('101',groups[0],'Reply only with ONE.')
            await emit('102',groups[0],'Reply only with TWO.')
            await emit('103',groups[1],'Use qq_history action stats and reply with this group ID and total archived message count.')
            deadline=time.monotonic()+360
            while len(replies)<4 and time.monotonic()<deadline: await asyncio.sleep(1)
            assert len(replies)>=4, f'Only {len(replies)} replies: {actions}'
            first=[r for r in replies if str(r['group'])==str(groups[0])]
            assert len(first)==3,first
            assert first[0]['reply_to']==['100'],first
            assert '437' in first[0]['text'],first
            assert first[1]['text'].strip()=='ONE' and first[2]['text'].strip()=='TWO',first
            print('STAGING END-TO-END PASSED',flush=True)
            reader.cancel();await asyncio.gather(reader,return_exceptions=True)

asyncio.run(main())
