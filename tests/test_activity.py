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

    def test_bounded_steps_no_raw_commands_reasoning_or_results(self):
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
            self.assertIn('不能据此判断卡死',activity.render('dm-42',0))

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
