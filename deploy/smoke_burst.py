"""Real-model same-second DM burst. Run ONLY in isolated staging, never live QQ."""
import asyncio
import json
import os
from pathlib import Path
import re
import time
import aiohttp


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    user=cfg['trusted_users'][0]
    replies=[]
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);result={}
                    action=request['action'];params=request['params']
                    if action=='send_private_msg':
                        text=''.join(s['data'].get('text','') for s in params['message'])
                        quote=[s['data']['id'] for s in params['message'] if s['type']=='reply']
                        assert params['user_id']==user
                        replies.append((quote,text))
                        print('BURST_REPLY',quote,text,flush=True)
                        result={'message_id':950000+len(replies)}
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            task=asyncio.create_task(reader())
            stamp=int(time.time())
            requests=[(867,[{'type':'text','data':{'text':'For this test, use terminal to create burst-marker.txt in your workspace containing BURST_DONE. A ZIP attachment and a follow-up request will arrive separately. Do not download or install the ZIP in this test. Reply briefly when the marker exists.'}}]),
                      (138,[{'type':'file','data':{'name':'test-skill.zip','file_id':'synthetic-file','file_size':10}}]),
                      (-215,[{'type':'text','data':{'text':'Please create burst-marker.txt containing BURST_DONE if it does not exist yet, otherwise confirm it is ready.'}}])]
            for mid,segments in requests:
                event={'post_type':'message','message_type':'private','sub_type':'friend',
                       'user_id':user,'message_id':mid,'time':stamp,
                       'sender':{'nickname':'Synthetic test'},'message':segments}
                await ws.send_json(event)
                await ws.send_json(event)  # Same-ID replay must be suppressed by the bridge.
            end=time.monotonic()+180
            while len(replies)<3:
                if time.monotonic()>end:raise TimeoutError('Burst test timed out')
                await asyncio.sleep(.5)
            await asyncio.sleep(2)
            assert len(replies)==3,replies
            assert [q for q,_ in replies]==[['867'],['138'],['-215']],replies
            for _,text in replies:
                assert not re.search(r'duplicat|repeated push|重复|重推|重发',text,re.I),text
            assert Path(f'/state/users/{user}/workspace/burst-marker.txt').read_text().strip()=='BURST_DONE'
            print('THREE UNIQUE ORDERED DM REPLIES; REAL REPLAYS FILTERED; NO FALSE DUPLICATE CLAIMS',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)


asyncio.run(main())
