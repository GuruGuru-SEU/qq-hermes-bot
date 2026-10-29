"""Verify model-driven original-path recovery on a synthetic site and fake QQ gateway."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import aiohttp

class Handler(BaseHTTPRequestHandler):
    handoffs=0
    alternate=0
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers()
        if self.path.startswith('/handoff'):
            type(self).handoffs+=1
            if self.handoffs==1:
                body='<title>Handoff</title><body>NEW<script>console.error("fixture initialization failed")</script>'
            elif 'fileId=123456789012345&source=original' in self.path:
                body='<title>Recovered</title><body>RECOVERY_OK original source preserved. File 123456789012345.'
            else:body='FILE_MISMATCH'
        elif self.path.startswith('/alternate'):
            type(self).alternate+=1;body='WRONG_ROUTE'
        else:
            body='<title>Workflow fixture</title><body><a href="/handoff?fileId=123456789012345&source=original&opaque=keep-this#/step">One-click export</a><a href="/alternate">Manual file upload</a>'
        self.wfile.write(body.encode())
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
                    {'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':f'请实际用浏览器打开 http://127.0.0.1:{server.server_port}/ ，按 One-click export 路径核实原始文件。本次必须保留原始来源，不能走 Manual file upload。若跳转白屏，用浏览器技能中的 diagnose 和 checkpoint/resume 精确恢复原始路径，最多恢复一次。这是短小模拟测试，没有真实下单或付款；看到恢复结果就简短回复，最后关闭浏览器。'}}]})
            await asyncio.wait_for(done.wait(),240)
            assert len(replies)==1,replies
            assert Handler.handoffs==2 and Handler.alternate==0,(Handler.handoffs,Handler.alternate,replies)
            import sqlite3
            path=Path('/opt/data/profiles/qq-900001/state.db')
            with sqlite3.connect(path) as db:
                calls='\n'.join(r[0] or '' for r in db.execute('SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL'))
            assert 'agent-browser' in calls and 'skill_view' in calls,calls[-3000:]
            for command in ('diagnose','checkpoint','resume','close'):assert command in calls,(command,calls[-3000:])
            assert not Path('/opt/qq-hermes-bot/qqhermes/quality.py').exists()
            assert 'answer_review' not in Path('/opt/data/profiles/qq-900001/logs/agent.log').read_text()
            print('REAL-MODEL RECOVERY PASSED: original source, diagnosis, exact resume, one reply, no reviewer',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    server.shutdown()

asyncio.run(main())
