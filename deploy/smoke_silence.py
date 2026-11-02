"""Real-model acknowledged follow-up completion; never real QQ sends."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

import aiohttp

gates = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        key = self.path.removeprefix('/')
        if key in gates:
            entered, release = gates[key]
            entered.set()
            release.wait(150)
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'ready')

    def log_message(self, *args): pass


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    assert cfg['trusted_users']==[900003,900004]
    server=ThreadingHTTPServer(('0.0.0.0',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}'
    replies=[]
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    call=json.loads(frame.data);action=call['action'];params=call['params'];result={}
                    if action in ('send_group_msg','send_private_msg'):
                        text=''.join(s['data'].get('text','') for s in params['message'])
                        quote=[s['data']['id'] for s in params['message'] if s['type']=='reply']
                        replies.append({'text':text,'quote':quote,'chat':params.get('group_id',params.get('user_id'))})
                        print(json.dumps(replies[-1],ensure_ascii=False),flush=True)
                        result={'message_id':990000+len(replies)}
                    elif action=='get_group_msg_history': result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':call['echo']})
            read=asyncio.create_task(reader())

            async def send(mid,text,chat=900001,user=1001,segments=None):
                dm=chat in (900003,900004)
                event={'post_type':'message','message_type':'private' if dm else 'group','sub_type':'friend',
                       'user_id':chat if dm else user,'message_id':mid,'time':time.time(),
                       'sender':{'nickname':'Alice' if user==1001 else 'Bob'},
                       'message':segments if segments is not None else ([{'type':'at','data':{'qq':'99'}}] if not dm else [])+[{'type':'text','data':{'text':text}}]}
                if not dm:event['group_id']=chat
                await ws.send_json(event)

            async def wait_until(predicate, timeout=240):
                deadline=time.monotonic()+timeout
                while not predicate():
                    if time.monotonic()>deadline:raise TimeoutError('Live inbox smoke timed out')
                    await asyncio.sleep(.25)

            def done(mid):
                with sqlite3.connect('/state/transport.sqlite3') as db:
                    row=db.execute('SELECT status FROM triggers WHERE message_id=?',('snowluma:'+str(mid),)).fetchone()
                return bool(row and row[0] not in ('queued','processing','accepted'))

            async def start_gate(mid, key, prompt, chat=900001):
                gates[key]=(threading.Event(),threading.Event())
                wait=f"python -c \"import urllib.request; urllib.request.urlopen('{url}/{key}', timeout=160).read()\""
                await send(mid,prompt+' 开始时先用 terminal 执行这个本地测试就绪检查（只执行一次）：'+wait,chat)
                await wait_until(gates[key][0].is_set)

            await start_gate(9101,'sphere','这是合成测试：检查返回后创建 sphere-ready.txt，内容 ready；最终必须用 qq_work finish 发送完成结果。')
            await send(9102,'这是一个独立的新任务：给Bob请安，然后继续原任务。本回归测试必须先把这条消息 resolve 为 relation=new（不是 refine），再用 qq_work report 的 kind=acknowledgement 发送这句问候（task_id 指向我这条消息），不使用 kind=result 发送问候。问候实际发送后，不要再次发送。',user=1001)

            def acknowledged():
                with sqlite3.connect('/state/transport.sqlite3') as db:
                    return db.execute("SELECT 1 FROM work_reports WHERE root_mid='snowluma:9101' AND target='snowluma:9102' AND kind='acknowledgement' AND status='sent'").fetchone()
            await wait_until(acknowledged,timeout=100)
            gates['sphere'][1].set()
            await wait_until(lambda:done(9101))
            with sqlite3.connect('/state/transport.sqlite3') as db:
                rows=db.execute("SELECT mid,state FROM work_items WHERE mid IN ('snowluma:9101','snowluma:9102')").fetchall()
                reports=db.execute("SELECT target,kind,status FROM work_reports WHERE root_mid='snowluma:9101'").fetchall()
            assert len(rows)==2 and all(r[1]=='completed' for r in rows),rows
            assert Path('/state/groups/900001/workspace/sphere-ready.txt').read_text().strip()=='ready'
            with sqlite3.connect('/state/transport.sqlite3') as db:
                sealed=db.execute("SELECT receipts FROM turn_completions WHERE root_mid='snowluma:9101'").fetchone()
            assert sealed and len(json.loads(sealed[0]))==2,sealed
            assert len(replies)==2,replies
            assert [r['quote'] for r in replies]==[['9102'],['9101']],replies
            assert all('silence marker' not in r['text'] and '[SILENT]' not in r['text'] for r in replies),replies
            print('PASS acknowledgement + original result; both tasks completed, two correctly quoted replies, no duplicated greeting or silence warning',flush=True)
            print('Reports:',reports,flush=True)
            read.cancel();await asyncio.gather(read,return_exceptions=True)
    server.shutdown()


asyncio.run(main())
