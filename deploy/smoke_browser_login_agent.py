"""Real Hermes, two synthetic QQ turns, external simulated login, no QQ sends."""
import asyncio
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import aiohttp
from smoke_browser_persistence import Handler

async def main():
    cfg = json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq'] == 99 and cfg['trusted_groups'] == [900001,900002]
    Handler.scanned = False
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    done = asyncio.Event(); replies = []
    async with aiohttp.ClientSession() as client:
        for attempt in range(30):
            try:
                async with client.get('http://127.0.0.1:8080/health') as response:
                    if response.status in (200, 503): break
            except aiohttp.ClientConnectionError:
                pass
            await asyncio.sleep(1)
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request = json.loads(frame.data); action = request['action']; params = request['params']; result = {}
                    if action == 'send_group_msg':
                        body = ''.join(s['data'].get('text','') for s in params['message'])
                        replies.append(body); print(json.dumps({'reply':body},ensure_ascii=False),flush=True)
                        result = {'message_id':990001+len(replies)}
                    elif action == 'set_msg_emoji_like' and not params['set']: done.set()
                    elif action == 'get_group_msg_history': result = {'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            task = asyncio.create_task(reader())
            async def send(mid, text):
                done.clear()
                await ws.send_json({'post_type':'message','message_type':'group','group_id':900001,'user_id':123456,
                    'message_id':mid,'time':time.time(),'sender':{'nickname':'Synthetic member'},'message':[
                        {'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':text}}]})
                await asyncio.wait_for(done.wait(),240)
            await send(1901,f'帮我登录这个测试网站 {base}/login ，使用名为 engineering 的账号档案。测试页无需发送二维码，我会在外部完成扫码。先保留页面等我告诉你完成；这轮不要替我完成登录，也不要一直等。之后我要查看账号页。')
            assert len(replies) == 1
            state = json.loads(Path('/opt/data/profiles/qq-900001/browser-state.json').read_text())
            assert state['lanes']['foreground']['login_until'] > time.time(), state
            Handler.scanned = True
            await asyncio.sleep(1)
            await send(1902,f'我已经扫好了，看看同一个页面是否登录成功，然后访问 {base}/account 确认账号页。帮我保留登录状态，最后关闭浏览器。')
            assert len(replies) == 2 and ('登录' in replies[-1] or 'Signed in' in replies[-1]), replies
            with sqlite3.connect('/opt/data/profiles/qq-900001/state.db') as db:
                calls='\n'.join(r[0] or '' for r in db.execute('SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL'))
            assert all(s in calls for s in ('login-wait','login-done','agent-browser close')), calls[-2000:]
            assert not Path('/tmp/qq-agent-browser-lease').read_text().strip()
            print('REAL-MODEL LOGIN HANDOFF PASSED: wait/return, same tab, verified login, close, one reply per turn',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    server.shutdown()

asyncio.run(main())
