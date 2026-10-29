"""Inspect an actual busy Hermes turn through a fake QQ gateway. Staging only."""
import asyncio
import json
import os
from pathlib import Path
import time
import aiohttp


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    marker=Path('/tmp/qq-status-smoke-started')
    marker.unlink(missing_ok=True)
    replies={};done=set()
    async with aiohttp.ClientSession() as client:
        for _ in range(45):
            try:
                async with client.get('http://127.0.0.1:8080/health') as response:
                    if response.status in (200,503):break
            except aiohttp.ClientConnectionError:pass
            await asyncio.sleep(1)
        async with client.ws_connect('http://127.0.0.1:8080/onebot',headers={'Authorization':'Bearer '+os.environ['ONEBOT_TOKEN']}) as ws:
            async def reader():
                async for frame in ws:
                    request=json.loads(frame.data);action=request['action'];params=request['params'];result={}
                    if action=='send_group_msg':
                        text=''.join(s['data'].get('text','') for s in params['message'])
                        reply=next((str(s['data']['id']) for s in params['message'] if s['type']=='reply'),'')
                        replies[reply]=text
                        print(json.dumps({'reply_to':reply,'group':params['group_id'],'text':text},ensure_ascii=False),flush=True)
                        result={'message_id':980000+len(replies)}
                    elif action=='set_msg_emoji_like' and not params['set']:done.add(str(params['message_id']))
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            reader_task=asyncio.create_task(reader())
            async def send(mid,text,group=900001):
                await ws.send_json({'post_type':'message','message_type':'group','group_id':group,'user_id':123456,
                    'message_id':mid,'time':time.time(),'sender':{'nickname':'Synthetic member'},'message':[
                        {'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':text}}]})
            async def until(predicate,timeout=120):
                end=time.monotonic()+timeout
                while not predicate():
                    assert not reader_task.done(),'Fake gateway disconnected'
                    if time.monotonic()>end:raise TimeoutError('Status smoke timed out')
                    await asyncio.sleep(.1)
            await send(3901,'这是短暂的前台工具测试。请直接使用 terminal 执行下列命令，timeout=40，不要使用后台模式，然后只回复 STATUS_DONE。命令：python -c "from pathlib import Path; import time; Path(\'/tmp/qq-status-smoke-started\').touch(); time.sleep(20); print(\'STATUS_TOOL_DONE\')"')
            await until(marker.exists)
            await send(3902,'这是一条独立排队消息，等前一条完成后只回复 QUEUE_DONE。')
            await send(3903,'#status')
            await until(lambda:'3903' in replies,timeout=8)
            status=replies['3903']
            assert '排队 1 条' in status and '运行 Python / uv' in status,status
            assert '运行中' in status and '分配中' not in status,status
            assert '3901' not in replies,'Status waited for foreground completion'
            await send(3904,'#status',group=900002)
            await until(lambda:'3904' in replies,timeout=8)
            assert '没有正在处理' in replies['3904'] and 'Python' not in replies['3904']
            await until(lambda:'3901' in done and '3902' in done)
            assert 'STATUS_DONE' in replies['3901'] and 'QUEUE_DONE' in replies['3902'],replies
            await send(3905,'#status')
            await until(lambda:'3905' in replies,timeout=8)
            assert '没有正在处理' in replies['3905'],replies['3905']
            print('STATUS SMOKE PASSED: actual model/tool receipts, immediate busy response, queue intact, other group isolated, idle cleanup',flush=True)
            pidfile=Path('/tmp/qq-stop-smoke-pid');finished=Path('/tmp/qq-stop-smoke-finished')
            pidfile.unlink(missing_ok=True);finished.unlink(missing_ok=True)
            await send(3911,'这是短暂的前台停止测试。请直接使用 terminal 执行下列命令，timeout=90，不要用后台模式，然后只回复 SHOULD_NOT_REPLY。命令：python -c "from pathlib import Path; import time,os; Path(\'/tmp/qq-stop-smoke-pid\').write_text(str(os.getpid())); time.sleep(60); Path(\'/tmp/qq-stop-smoke-finished\').touch()"')
            await until(pidfile.exists)
            pid=int(pidfile.read_text())
            await send(3912,'这是一条新的独立消息。前一条停止后无需继续旧任务，只回复 AFTER_STOP_OK。')
            await send(3913,'#stop')
            await until(lambda:'3913' in replies,timeout=20)
            assert '已中断当前回复' in replies['3913'] and 'Python' in replies['3913'],replies['3913']
            assert '后续 1 条' in replies['3913'],replies['3913']
            def process_dead():
                try:os.kill(pid,0);return False
                except ProcessLookupError:return True
            await until(process_dead,timeout=10)
            await until(lambda:'3912' in done)
            assert 'AFTER_STOP_OK' in replies['3912'],replies
            assert '3911' not in replies and not finished.exists(),replies
            await send(3914,'#status')
            await until(lambda:'3914' in replies,timeout=8)
            assert '没有正在处理' in replies['3914']
            print('STOP SMOKE PASSED: running process reaped, no stale reply, next queued request completes, status idle',flush=True)
            reader_task.cancel();await asyncio.gather(reader_task,return_exceptions=True)


asyncio.run(main())
