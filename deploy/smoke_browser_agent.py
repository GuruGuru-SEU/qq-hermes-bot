"""Verify native Hermes discovers and uses the browser skill on a fake QQ gateway."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import aiohttp

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers()
        self.wfile.write(b'''<!doctype html><title>Interactive fixture</title><h1>Browser test</h1>
<label>Number <input id="n" aria-label="Number"></label><button onclick="document.querySelector('#out').textContent='RESULT '+(Number(document.querySelector('#n').value)*7)">Calculate</button><p id="out">Not calculated</p>''')
    def log_message(self,*a):pass

async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    replies=[];done=asyncio.Event()
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);action=request['action'];params=request['params'];result={}
                    if action=='send_group_msg':
                        body=''.join(s['data'].get('text','') for s in params['message'])
                        replies.append(body);print(json.dumps({'reply':body},ensure_ascii=False),flush=True)
                        result={'message_id':980001+len(replies)}
                    elif action=='set_msg_emoji_like' and not params['set']:done.set()
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            task=asyncio.create_task(reader())
            await ws.send_json({'post_type':'message','message_type':'group','group_id':900001,'user_id':123456,
                'message_id':901,'time':time.time(),'sender':{'nickname':'Synthetic member'},'message':[
                    {'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':f'请实际打开这个测试页面 http://127.0.0.1:{server.server_port}/ ，用浏览器在 Number 输入 23，再点击 Calculate，告诉我页面显示的结果。最后关闭浏览器。不要仅靠心算或读取源码代替操作。'}}]})
            await asyncio.wait_for(done.wait(),240)
            assert len(replies)==1 and '161' in replies[0],replies
            import sqlite3
            path=Path('/opt/data/profiles/qq-900001/state.db')
            with sqlite3.connect(path) as db:
                calls='\n'.join(r[0] or '' for r in db.execute('SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL'))
            assert 'agent-browser' in calls and 'skill_view' in calls,calls[-3000:]
            assert 'snapshot' in calls and 'click' in calls and 'close' in calls,calls[-3000:]
            print('REAL-MODEL BROWSER SKILL PASSED: discovery, browser interaction, one reply, close',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    server.shutdown()

asyncio.run(main())
