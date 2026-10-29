import unittest
from unittest.mock import patch

import test_personal
from qqhermes import activity


class ActivityTests(unittest.TestCase):
    setUp=test_personal.PersonalTests.setUp

    def tearDown(self):
        activity.finish('dm-42','100')
        activity.finish('dm-43','100')
        test_personal.PersonalTests.tearDown(self)

    def test_trigger_is_a_token_not_url_or_legacy_command(self):
        for text in ('#status','please #status now','#STATUS！','\n#status\n'):
            self.assertTrue(activity.is_status(text),text)
        for text in ('/status','xxx.com/status','https://x.test/#status','https://x.test?q=#status',
                     '#statusfoo','foo#status','##status','#status/path','#status.html'):
            self.assertFalse(activity.is_status(text),text)
        for text in ('#stop',' #STOP ','#stop！'):self.assertTrue(activity.is_stop(text),text)
        for text in ('https://example.com/#stop','do not #stop','请解释 #stop','#kill'):
            self.assertFalse(activity.is_stop(text),text)

    def test_progress_lifecycle_scoping_and_late_events(self):
        activity.begin('dm-42','100','42')
        activity.begin('dm-43','100','43')
        activity.model_request({},session_id='main-session',turn_id='turn')
        activity.tool_started('web_extract',{'url':'https://user:password@example.com/a?token=SECRET'},
                              session_id='main-session',tool_call_id='fetch')
        text=activity.render('dm-42',2)
        self.assertIn('排队 2 条',text);self.assertIn('网页抓取 · example.com',text)
        self.assertIn('运行中',text);self.assertNotIn('password',text);self.assertNotIn('SECRET',text)
        activity.tool_finished('web_extract',result='SECRET',session_id='main-session',tool_call_id='fetch',status='error')
        self.assertIn('失败',activity.render('dm-42',0))
        activity.tool_started('terminal',{'command':'agent-browser --account test click @e5'},
                              session_id='main-session',tool_call_id='click')
        self.assertIn('浏览器点击',activity.render('dm-42',0))
        self.assertNotIn('浏览器点击',activity.render('dm-43',0))
        activity.tool_started('write_file',{},session_id='child-session',tool_call_id='late')
        self.assertNotIn('写入文件',activity.render('dm-42',0))
        activity.finish('dm-42','wrong-message')
        self.assertIsNotNone(activity.snapshot('dm-42'))
        activity.finish('dm-42','100')
        activity.tool_started('terminal',{},session_id='main-session',tool_call_id='late')
        self.assertIsNone(activity.snapshot('dm-42'))
        self.assertIn('没有正在处理',activity.render('dm-42',0))

    def test_bounded_steps_redact_secrets_reasoning_and_results(self):
        activity.begin('dm-42','100','42')
        secret='PRIVATE_REASONING_AND_CREDENTIALS'
        activity.model_request({'messages':[{'reasoning_content':secret}]},session_id='s',turn_id='t')
        for i in range(20):
            activity.tool_started('terminal',{'command':'curl https://x.test?token='+secret},session_id='s',tool_call_id=str(i))
            activity.tool_finished('terminal',result=secret,session_id='s',tool_call_id=str(i),status='success')
        snap=activity.snapshot('dm-42')
        self.assertEqual(len(snap['steps']),8);self.assertEqual(snap['running'],{})
        self.assertNotIn(secret,str(snap))
        with patch('qqhermes.activity.time.monotonic',return_value=snap['updated']+90):
            self.assertIn('最近活动：90 秒前',activity.render('dm-42',0))
            self.assertNotIn('不能据此判断卡死',activity.render('dm-42',0))

    def test_developer_details_and_progress_are_ordered_without_replaying_provider(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        activity.begin('dm-42','100','42')
        activity.tool_started('qq_file',{'action':'download','message_id':'snowluma:25','position':0},tool_call_id='f')
        activity.tool_finished('qq_file',tool_call_id='f',status='success')
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='文件已下载，接下来检查脚本。',reasoning_content='PRIVATE_THOUGHTS',tool_calls=[{'id':'read'}]))])
        next_call=Mock(return_value=response)
        self.assertIs(activity.model_execution({},next_call,session_id='main',turn_id='t'),response)
        next_call.assert_called_once_with({})
        activity.tool_started('read_file',{'path':'/state/users/42/workspace/main.py','offset':10,'limit':20},session_id='main',tool_call_id='r')
        activity.tool_finished('read_file',session_id='main',tool_call_id='r',status='success')
        activity.tool_started('terminal',{'command':'python main.py --output result.png','cwd':'/state/users/42/workspace'},session_id='main',tool_call_id='c')
        text=activity.render('dm-42',0)
        for expected in ('snowluma:25','文件已下载','/state/users/42/workspace/main.py','起始行 10','python main.py --output result.png'):
            self.assertIn(expected,text)
        self.assertLess(text.index('文件已下载'),text.index('读取文件'))
        self.assertNotIn('PRIVATE_THOUGHTS',str(activity.snapshot('dm-42')))
        self.assertNotIn('文件已下载',activity.render('dm-43',0))

    def test_model_observer_excludes_final_and_thinking_blocks_and_survives_failures(self):
        from unittest.mock import Mock
        activity.begin('dm-42','100','42')
        for message in ({'content':'FINAL','tool_calls':[]},
                        {'content':[{'type':'thinking','text':'PRIVATE'}],'tool_calls':[{}]},
                        {'reasoning_content':'PRIVATE','tool_calls':[{}]}):
            activity.model_execution({},Mock(return_value={'choices':[{'message':message}]}),turn_id='t')
        self.assertEqual(len(activity.snapshot('dm-42')['steps']),0)
        failing=Mock(side_effect=TimeoutError('provider timeout'))
        with self.assertRaises(TimeoutError):activity.model_execution({},failing,turn_id='t')
        failing.assert_called_once()

    def test_preview_redacts_before_truncating_and_preserves_normal_commands(self):
        cmd='curl -H "Authorization: Bearer demo-auth-secret" https://alice:demo-pass@example.com/file?token=demo-token --password "demo password" --api-key=demo-key'
        result=activity.preview(cmd)
        for secret in ('demo-auth-secret','demo-pass','demo-token','demo password','demo-key'):
            self.assertNotIn(secret,result)
        self.assertEqual(activity.preview('python script.py --width 1280'),'python script.py --width 1280')
        self.assertIn('已截断',activity.preview('x'*500))
        self.assertIn('未展示',activity.preview('x'*40000))
        self.assertNotIn('\n',activity.preview('echo a\necho b'))

    def test_compression_before_model_call_and_session_rotation(self):
        from gateway.session_context import get_session_env
        activity.begin('dm-42','100','42')
        activity.phase('dm-42','100','Hermes 正在准备会话／整理上下文')
        self.assertNotIn('分配中',activity.render('dm-42',0))
        activity.auxiliary_started(aux_task='compression',session_id='old',api_request_id='a',approx_input_tokens=44000)
        text=activity.render('dm-42',0)
        self.assertIn('正在压缩',text);self.assertIn('44,000',text);self.assertIn('模型请求 0 次',text)
        activity.auxiliary_finished(aux_task='compression',session_id='old',api_request_id='a')
        with patch('gateway.session_context.get_session_env',side_effect=lambda key,default='':'new' if key=='HERMES_SESSION_ID' else get_session_env(key,default)):
            activity.model_request({},session_id='new',turn_id='t')
        self.assertEqual(activity.snapshot('dm-42')['session'],'new')
        self.assertIn('模型请求 1 次',activity.render('dm-42',0))
        activity.auxiliary_finished(aux_task='compression',session_id='old',api_request_id='a',error='late')
        self.assertNotIn('摘要失败',activity.render('dm-42',0))

    def test_cron_delegation_and_wrong_message_are_ignored(self):
        activity.begin('dm-42','100','42')
        with patch('agent.delegation_context.is_delegated_child_context',return_value=True):
            activity.tool_started('web_search',{},tool_call_id='child')
        for kwargs in ({'message_id':'101'},{'cron_session':'1'}):
            # Test context readers directly; set_session_vars has no public cron flag.
            from gateway.session_context import get_session_env
            field='HERMES_SESSION_MESSAGE_ID' if 'message_id' in kwargs else 'HERMES_CRON_SESSION'
            value=next(iter(kwargs.values()))
            with patch('gateway.session_context.get_session_env',side_effect=lambda key,default='':value if key==field else get_session_env(key,default)):
                activity.tool_started('web_search',{},tool_call_id='other')
        self.assertEqual(len(activity.snapshot('dm-42')['steps']),0)

    def test_namespaced_plugin_modules_share_only_scoped_receipts(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('qqhermes._activity_test_namespace',activity.__file__)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        activity.begin('dm-42','100','42')
        module.tool_started('web_search',{},session_id='main',tool_call_id='search')
        self.assertIn('网页搜索',activity.render('dm-42',0))
        self.assertIn('没有正在处理',module.render('dm-43',0))
