"""Synthetic OneBot regression using the real model, never live QQ."""
import asyncio
import json
import os
from pathlib import Path
import time
import aiohttp
from PIL import Image,ImageDraw
from qqhermes.history import History

TEXT='今天这场雨终于停了，我准备去楼下走走，顺便买点新鲜水果。\n\n你上次推荐的电影我还记着，晚上正好找时间看看。\n\n阳台的薄荷长出了新叶，明天可以试着泡杯茶。'

async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    group=900001;sent=[];cleared=set();archive=History(Path('/state/groups')/str(group)/'history.sqlite3')
    def seed_image(mid,color,age=0,mention=False):
        event={'post_type':'message','message_type':'group','group_id':group,'user_id':123456,
            'message_id':mid,'time':time.time()-age,'sender':{'nickname':'Synthetic member'},
            'message':[{'type':'image','data':{'file':'synthetic'}}]}
        if mention:event['message'] += [{'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':'这张图片是什么颜色？只说颜色。'}}]
        directory=archive.path.parent/'media';directory.mkdir(exist_ok=True)
        path=directory/f'{mid}.png';image=Image.new('RGB',(640,480),color);ImageDraw.Draw(image).rectangle((80,80,560,400),outline='black',width=12);image.save(path)
        archive.save({**event,'message_id':f'snowluma:{mid}'})
        archive.image_result(f'snowluma:{mid}',0,'ready',path)
        return event
    seed_image(810,'red',age=900)
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);action=request['action'];params=request['params'];result={}
                    if action in ('send_group_msg','send_private_msg'):
                        segments=params['message'];quote=next((str(x['data']['id']) for x in segments if x['type']=='reply'),None)
                        body=''.join(x['data'].get('text','') for x in segments)
                        sent.append((quote,body));print(json.dumps({'quote':quote,'text':body},ensure_ascii=False),flush=True)
                        result={'message_id':970000+len(sent)}
                    elif action=='set_msg_emoji_like' and not params['set']:cleared.add(str(params['message_id']))
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            reader_task=asyncio.create_task(reader())
            async def ask(mid,text=None,event=None):
                before=len(sent)
                if event is None:
                    event={'post_type':'message','message_type':'group','group_id':group,'user_id':123456,
                        'message_id':mid,'time':time.time(),'sender':{'nickname':'Synthetic member'},
                        'message':[{'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':text}}]}
                await ws.send_json(event)
                deadline=time.monotonic()+180
                while str(mid) not in cleared:
                    if reader_task.done():await reader_task
                    if time.monotonic()>deadline:raise TimeoutError(mid)
                    await asyncio.sleep(.25)
                answers=sent[before:]
                assert len(answers)==1 and answers[0][0]==str(mid),answers
                return answers[0][1]
            answer=await ask(811,'请只原样回复下面三个自然段，不加其他内容：\n'+TEXT)
            assert ''.join(answer.split())==''.join(TEXT.split()),answer
            answer=await ask(812,event=seed_image(812,'blue',mention=True))
            assert '蓝' in answer and '红' not in answer,answer
            answer=await ask(813,'今天早饭我吃了一个包子，午饭又吃了两个包子，合计吃了几个包子？简短回答。')
            assert any(x in answer for x in ('三','3')) and not any(x in answer for x in ('图片','颜色','蓝','红')),answer
            await ws.send_json(seed_image(814,'green'))
            answer=await ask(815,'我刚发的这张图片是什么颜色？只说颜色。')
            assert '绿' in answer and '蓝' not in answer,answer
            from qqhermes.store import Store
            store=Store(Path(os.environ['QQ_STATE'])/'transport.sqlite3')
            with store.db() as db:
                rows=db.execute('SELECT parts FROM outbound_sequences').fetchall()
                assert all(len(json.loads(r['parts']))==1 for r in rows)
            print('CONTEXT SMOKE PASSED: single send, own image, unrelated follow-up, fresh referenced image',flush=True)
            reader_task.cancel();await asyncio.gather(reader_task,return_exceptions=True)

asyncio.run(main())
