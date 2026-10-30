"""Real-model follow-up scenarios in isolated staging; never real QQ sends."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

import aiohttp
from PIL import Image, ImageColor
from xml.etree import ElementTree

gates = {}
image = io.BytesIO()
Image.new('RGB', (160, 160), 'red').save(image, format='PNG')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        key = self.path.removeprefix('/')
        if key in gates:
            entered, release = gates[key]
            entered.set()
            release.wait(150)
        self.send_response(200)
        self.send_header('Content-Type', 'image/png' if key == 'red.png' else 'text/plain')
        self.end_headers()
        self.wfile.write(image.getvalue() if key == 'red.png' else b'ready')

    def log_message(self, *args): pass


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    assert cfg['trusted_users']==[900003,900004]
    server=ThreadingHTTPServer(('0.0.0.0',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    # The fixture download must pass the bridge's normal SSRF validation. Image
    # case below seeds a validated cache entry instead of bypassing URL guards.
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

            # Order change + requester approval while the first tool is active.
            await start_gate(101,'order','这是模拟订单，不下单不花钱。请准备原材料A的订单，总价35元，预算50元。检查完成后将最终材料和总价写到 order.json，键 material、total；再简短告知结果。')
            await send(102,'换成材料B吧，比原来贵10元。',user=1002)
            await send(103,'按照Bob说的做吧，仍然不能超过50元。')
            # The agent must incorporate the follow-ups BEFORE this running
            # terminal exits. Merely consuming them after tool completion fails.
            def classified(mid):
                with sqlite3.connect('/state/transport.sqlite3') as db:
                    row=db.execute('SELECT relation FROM live_requests WHERE mid=?',('snowluma:'+str(mid),)).fetchone()
                return bool(row and row[0])
            started=time.monotonic()
            await wait_until(lambda:classified(102) and classified(103),timeout=100)
            print(f'PASS live correction handled while terminal still running ({time.monotonic()-started:.1f}s)',flush=True)
            gates['order'][1].set();await wait_until(lambda:done(101))
            order=json.loads(Path('/state/groups/900001/workspace/order.json').read_text())
            assert order['total']==45 and 'B' in order['material'],order
            with sqlite3.connect('/state/transport.sqlite3') as db:
                rows=db.execute("SELECT mid,owner,relation FROM live_requests WHERE root_mid='snowluma:101'").fetchall()
            assert ('snowluma:102','1002','proposal') in rows,rows
            assert ('snowluma:103','1001','approve') in rows,rows
            print('PASS group proposal + owner approval + retained budget',flush=True)

            # A DM refinement plus an independent task: two results, no per-message replies.
            start=len(replies)
            await start_gate(201,'draw','请在工作区创建一个蓝色圆形的 circle.svg，画好后告诉我。',900003)
            await send(202,'圆形改成红色。',900003)
            await send(203,'另外帮我写一首两行的春天小诗。',900003)
            gates['draw'][1].set();await wait_until(lambda:done(201))
            svg=Path('/state/users/900003/workspace/circle.svg').read_text().lower()
            circle = ElementTree.fromstring(svg).find('{http://www.w3.org/2000/svg}circle')
            red, green, blue = ImageColor.getrgb(circle.attrib['fill'])
            assert red > 150 and red > 2*green and red > 2*blue,svg
            texts=[r for r in replies[start:] if r['text']]
            assert any(r['quote']==['201'] for r in texts),texts
            assert any(r['quote']==['203'] for r in texts),texts
            assert len(texts)<=3,texts
            print('PASS DM refinement + independent poem results',flush=True)

            # Replacing an active request does not execute its superseded file write.
            await start_gate(301,'cancel','请创建 cancelled.svg，内容是蓝色圆形。',900004)
            await send(302,'不要画图了，不要创建那个文件，改成写一首两行小诗。',900004)
            gates['cancel'][1].set();await wait_until(lambda:done(301))
            assert not Path('/state/users/900004/workspace/cancelled.svg').exists()
            with sqlite3.connect('/state/transport.sqlite3') as db:
                relation=db.execute("SELECT relation FROM live_requests WHERE mid='snowluma:302'").fetchone()
            assert relation==('replace',),relation
            print('PASS requester cancellation/replacement',flush=True)

            # Standalone image + question: one answer, tied to current image.
            start=len(replies)
            await send(401,'',900003,segments=[{'type':'image','data':{'file':'synthetic-red-image'}}])
            from qqhermes.history import History
            archive=History(Path('/state/users/900003/history.sqlite3'))
            await wait_until(lambda:bool(archive.images('snowluma:401')),timeout=5)
            path=archive.path.parent/'media'/'synthetic-red.png';path.parent.mkdir(exist_ok=True)
            path.write_bytes(image.getvalue());archive.image_result('snowluma:401',0,'ready',path)
            await send(402,'刚发的图片主体是什么颜色？只要简短回答。',900003)
            await wait_until(lambda:done(401))
            texts=[r for r in replies[start:] if r['text']]
            assert len(texts)==1 and ('红' in texts[0]['text'] or 'red' in texts[0]['text'].lower()),texts
            print('PASS image + question = one answer',flush=True)

            # Bob's unaddressed group image is context, then his addressed
            # question becomes a separate task in Alice's still-running turn.
            start=len(replies)
            await start_gate(501,'group-image','检查完成后请简短告诉我检查结果。',900002)
            await send(502,'',900002,user=1002,segments=[{'type':'image','data':{'file':'synthetic-red-image'}}])
            archive=History(Path('/state/groups/900002/history.sqlite3'))
            await wait_until(lambda:bool(archive.images('snowluma:502')),timeout=5)
            path=archive.path.parent/'media'/'synthetic-red.png';path.parent.mkdir(exist_ok=True)
            path.write_bytes(image.getvalue());archive.image_result('snowluma:502',0,'ready',path)
            await send(503,'我刚发的图片是什么颜色？请看图后简短回答。',900002,user=1002)
            gates['group-image'][1].set();await wait_until(lambda:done(501))
            texts=[r for r in replies[start:] if r['text']]
            assert any(r['quote']==['501'] for r in texts),texts
            assert any(r['quote']==['503'] and ('红' in r['text'] or 'red' in r['text'].lower()) for r in texts),texts
            with sqlite3.connect('/state/transport.sqlite3') as db:
                owner=db.execute("SELECT owner FROM work_items WHERE chat='900002' AND mid='snowluma:503'").fetchone()
            assert owner==('1002',),owner
            print('PASS group other-sender image + independent task',flush=True)

            # Approval after the previous reply must reference the same owner's
            # recorded proposal, without losing the original budget constraint.
            await start_gate(701,'later-approval','这是新的独立模拟订单，不下单不花钱。请准备原材料A的订单，总价35元，预算50元。检查完成后将最终材料和总价写到 later-order.json，键 material、total；再简短告知结果。')
            await send(702,'这个新订单换成材料B吧，比原来贵10元。',user=1002)
            gates['later-approval'][1].set();await wait_until(lambda:done(701))
            order=json.loads(Path('/state/groups/900001/workspace/later-order.json').read_text())
            assert order['total']==35 and 'A' in order['material'],order
            await send(703,'刚才这个新订单按照Bob说的做吧，把材料换成B。')
            await wait_until(lambda:done(703))
            order=json.loads(Path('/state/groups/900001/workspace/later-order.json').read_text())
            assert order['total']==45 and 'B' in order['material'],order
            with sqlite3.connect('/state/transport.sqlite3') as db:
                approval=db.execute("SELECT relation,target FROM live_requests WHERE mid='snowluma:703'").fetchone()
            assert approval==('approve','snowluma:701'),approval
            print('PASS later-turn approval preserves budget; all live inbox cases passed',flush=True)
            read.cancel();await asyncio.gather(read,return_exceptions=True)
    server.shutdown()


asyncio.run(main())
