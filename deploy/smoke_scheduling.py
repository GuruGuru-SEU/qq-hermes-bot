"""Real-model scheduling/management and timed delivery to a fake QQ gateway only."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time
import aiohttp


async def main():
    cfg=json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq']==99 and cfg['trusted_groups']==[900001,900002]
    home=Path('/opt/data/profiles/qq-900001')
    replies=[];done=asyncio.Event()
    def jobs():
        path=home/'cron/jobs.json'
        if not path.exists():return []
        return json.loads(path.read_text())['jobs']
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
                        assert params['group_id']==900001,params
                        body=''.join(s['data'].get('text','') for s in params['message'])
                        replies.append(body)
                        print(json.dumps({'reply':body},ensure_ascii=False),flush=True)
                        result={'message_id':970000+len(replies)}
                    elif action=='set_msg_emoji_like' and not params['set']:done.set()
                    elif action=='get_group_msg_history':result={'messages':[]}
                    await ws.send_json({'status':'ok','retcode':0,'data':result,'echo':request['echo']})
            task=asyncio.create_task(reader())
            async def send(mid,text):
                done.clear()
                await ws.send_json({'post_type':'message','message_type':'group','group_id':900001,'user_id':123456,
                    'message_id':mid,'time':time.time(),'sender':{'nickname':'Synthetic member'},'message':[
                        {'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':text}}]})
                await asyncio.wait_for(done.wait(),240)
                assert not task.done(),'Fake gateway disconnected'
            await send(2901,'帮我设个名叫“喝水测试”的提醒，每10分钟发一次，一共3次，每次只说“喝杯水，休息一下。”，不需要现在发。')
            assert len(replies)==1,replies
            assert len(jobs())==1,jobs()
            job=jobs()[0];ident=job['id'];script=job['script']
            assert job['no_agent'] and job['repeat']['times']==3,job
            assert script.startswith('qq-reminder-'),job
            assert job['origin']['chat_id']=='900001' and job['failure_deliver']=='local',job
            await send(2902,'把“喝水测试”改成每分钟一次，并让到时发出的提醒进入本群对话上下文，方便接着聊。其他保持不变。')
            assert len(jobs())==1 and jobs()[0]['id']==ident,jobs()
            assert jobs()[0]['script']==script and jobs()[0]['repeat']['times']==3,jobs()
            assert jobs()[0]['attach_to_session'],jobs()
            end=time.monotonic()+150
            while '喝杯水，休息一下。' not in replies:
                assert time.monotonic()<end,'Timed reminder was not delivered'
                await asyncio.sleep(.5)
            assert all('Cronjob Response' not in text for text in replies),replies
            await send(2903,'先暂停“喝水测试”，告诉我上次是否成功，不用删除。')
            assert not jobs()[0]['enabled'],jobs()
            assert jobs()[0]['last_status']=='ok',jobs()
            with sqlite3.connect(home/'state.db') as db:
                calls='\n'.join(row[0] or '' for row in db.execute('SELECT tool_calls FROM messages WHERE tool_calls IS NOT NULL'))
                mirrored=[r[0] for r in db.execute("SELECT content FROM messages WHERE role='user' AND content LIKE '%[Cron delivery:%'")]
            assert any('喝杯水，休息一下。' in text for text in mirrored),mirrored
            assert 'skill_view' in calls and 'qq-scheduling' in calls,calls[-2000:]
            assert 'cronjob_manage' in calls and 'qq-reminder --text-file' in calls,calls[-2000:]
            other=Path('/opt/data/profiles/qq-900002/cron/jobs.json')
            assert not other.exists() or not json.loads(other.read_text())['jobs']
            print('REAL-MODEL SCHEDULING PASSED: skill discovery, no-agent reminder, finite repeat, update same job, timed plain delivery, continuation mirror, pause/status, group isolation',flush=True)
            task.cancel();await asyncio.gather(task,return_exceptions=True)


asyncio.run(main())
