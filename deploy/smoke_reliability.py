"""Real-model checks. Run only in a fresh staging container without a QQ connection."""
import asyncio
import json
import os
from pathlib import Path
import time

import aiohttp
from qqhermes.history import History
from qqhermes.store import Store


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    group=cfg['trusted_groups'][0]
    profile=Path(os.environ['HERMES_HOME'])/'profiles'/f'qq-{group}'
    (profile/'memories/MEMORY.md').write_text('Never append FORBIDDEN_END to a reply. Keep replies short.')
    store=Store(Path(os.environ['QQ_STATE'])/'transport.sqlite3')
    archive=History(Path(os.environ['QQ_STATE'])/'groups'/str(group)/'history.sqlite3')
    archive.save({'message_id':'reliability-archive','time':time.time()-86400,'user_id':555555,
                  'sender':{'nickname':'Fixture'},'message':[{'type':'text','data':{'text':'reliability-needle public test fixture number is 8273'}}]})
    def preference(name):
        with store.db() as db:
            db.execute('INSERT OR REPLACE INTO members VALUES (?,?,?,?)',
                       (group,'123456','Preferred name: '+name,time.time()))
            db.execute('INSERT OR REPLACE INTO members VALUES (?,?,?,?)',
                       (group,'222222','Preferred name: OTHER_MEMBER',time.time()))
    replies=[];uploads=[]
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def read():
                async for frame in ws:
                    data=json.loads(frame.data);action=data.get('action');result={}
                    if action=='send_group_msg':
                        segments=data['params']['message']
                        reply={'text':''.join(s['data'].get('text','') for s in segments),
                               'reply_to':[s['data']['id'] for s in segments if s['type']=='reply']}
                        replies.append(reply);print(json.dumps(reply,ensure_ascii=False),flush=True)
                        result={'message_id':f'reliability-reply-{len(replies)}'}
                    elif action=='get_group_msg_history':result={'messages':[]}
                    elif action=='upload_group_file':
                        from urllib.parse import urlsplit
                        path=urlsplit(data['params']['file']).path
                        async with client.get('http://127.0.0.1:8080'+path) as response:
                            assert response.status==200
                            assert (await response.read()).decode().strip()=='WORKSPACE_OK'
                        uploads.append(data['params']['name'])
                        result={}  # A successful upload need not return a message ID.
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':data['echo']})
            reader=asyncio.create_task(read())
            async def request(mid,text):
                count=len(replies)
                await ws.send_json({'post_type':'message','message_type':'group','group_id':group,'user_id':123456,
                    'message_id':mid,'time':time.time(),'sender':{'nickname':'Staging'},
                    'message':[{'type':'at','data':{'qq':str(cfg['bot_qq'])}},{'type':'text','data':{'text':text}}]})
                async with asyncio.timeout(180):
                    while len(replies)==count:await asyncio.sleep(.5)
                return replies[count]['text']
            try:
                preference('FIRST_PREFERENCE')
                first=await request('reliability-1','What is my saved preferred name? Answer only with that name.')
                assert first.strip()=='FIRST_PREFERENCE',first
                preference('SECOND_PREFERENCE')
                second=await request('reliability-2','What is my current saved preferred name? Answer only with that name.')
                assert second.strip()=='SECOND_PREFERENCE',second
                third=await request('reliability-3','Use qq_history action search with contains reliability-needle and user 555555 to find the public test fixture number. Reply only with the number.')
                assert third.strip()=='8273',third
                output=Path(os.environ['QQ_STATE'])/'groups'/str(group)/'workspace/native-write-check.txt'
                fourth=await request('reliability-4',f'Use write_file to write exactly WORKSPACE_OK to {output}, then use read_file to verify it. Do not use terminal. Reply only WORKSPACE_OK after verifying.')
                assert output.read_text().strip()=='WORKSPACE_OK',fourth
                fifth=await request('reliability-5',f'Upload {output} to this group using qq_send kind file reason requested, once. Reply only UPLOAD_OK after success.')
                assert fifth.strip()=='UPLOAD_OK',fifth
                assert uploads==['native-write-check.txt'],uploads
                assert all('OTHER_MEMBER' not in r['text'] and 'FORBIDDEN_END' not in r['text'] for r in replies)
                print('STAGING RELIABILITY PASSED: fresh preferences, filtered history, native file write/read, one successful upload without message ID',flush=True)
            finally:
                reader.cancel();await asyncio.gather(reader,return_exceptions=True)


asyncio.run(main())
