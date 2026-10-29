import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqhermes.browser import prepare,scope,run


class BrowserScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);cfg=self.root/'config.json'
        cfg.write_text(json.dumps({'trusted_groups':[1,2],'trusted_users':[42]}))
        self.env=patch.dict(os.environ,{'QQ_CONFIG':str(cfg),'QQ_STATE':str(self.root/'state')});self.env.start();self.addCleanup(self.env.stop)

    def environment(self,profile='qq-1',session='main',message='m1'):
        return {'HERMES_HOME':str(self.root/'profiles'/profile),'HERMES_SESSION_ID':session,
            'HERMES_SESSION_MESSAGE_ID':message,'DEEPSEEK_API_KEY':'never-pass','ONEBOT_TOKEN':'never-pass',
            'AGENT_BROWSER_SESSION':'other-session','AGENT_BROWSER_CDP':'9222','PATH':'/usr/bin'}

    def test_chat_dm_and_background_sessions_are_distinct_and_credentials_scrubbed(self):
        _,a,owner=prepare(['open','https://example.com'],self.environment())
        _,b,_=prepare(['open','https://example.com'],self.environment('qq-2'))
        _,c,_=prepare(['open','https://example.com'],self.environment('qq-dm-42'))
        _,child,co=prepare(['--job','report','open','https://example.com'],self.environment(session='child'))
        self.assertNotEqual(a['AGENT_BROWSER_NAMESPACE'],b['AGENT_BROWSER_NAMESPACE'])
        self.assertNotEqual(a['HOME'],c['HOME'])
        self.assertNotEqual(a['AGENT_BROWSER_SESSION'],child['AGENT_BROWSER_SESSION'])
        self.assertNotIn('DEEPSEEK_API_KEY',a);self.assertNotIn('ONEBOT_TOKEN',a);self.assertNotIn('AGENT_BROWSER_CDP',a)
        self.assertNotIn('AGENT_BROWSER_PROFILE',a)
        self.assertLess(len(a['AGENT_BROWSER_SOCKET_DIR']+'/'+a['AGENT_BROWSER_SESSION']+'.sock'),80)
        self.assertTrue(Path(a['AGENT_BROWSER_DOWNLOAD_PATH']).is_relative_to(self.root/'state/groups/1/workspace'))

    def test_persistence_is_explicit_and_chat_scoped(self):
        args,env,owner=prepare(['--job','report','--persistent','open','https://example.com'],self.environment())
        self.assertEqual(args,['open','https://example.com'])
        self.assertTrue(Path(env['AGENT_BROWSER_PROFILE']).is_relative_to(self.root/'profiles/qq-1'))
        self.assertTrue(owner['persistent'])
        _,other,_=prepare(['--persistent','close'],self.environment('qq-2'))
        self.assertNotEqual(env['AGENT_BROWSER_PROFILE'],other['AGENT_BROWSER_PROFILE'])

    def test_untrusted_or_cross_session_controls_are_rejected(self):
        for args in (['--session','foreign','open','https://example.com'],['--profile=/foreign','open','x'],
                     ['close','--all'],['connect','9222'],['dashboard'],['--job','../foreign','close']):
            with self.assertRaises(ValueError):prepare(args,self.environment())
        for profile in ('qq-999','qq-dm-999','default'):
            with self.assertRaises((ValueError,PermissionError)):prepare(['close'],self.environment(profile))

    def test_only_one_job_lease_and_close_releases_it(self):
        lease=self.root/'lease'
        with patch('qqhermes.browser.LEASE',lease),patch('qqhermes.browser.subprocess.run') as execute:
            execute.return_value.returncode=0
            with patch.dict(os.environ,self.environment()):self.assertEqual(run(['open','https://example.com']),0)
            with patch.dict(os.environ,self.environment('qq-2')):self.assertEqual(run(['open','https://example.com']),75)
            self.assertEqual(execute.call_count,1)
            with patch.dict(os.environ,self.environment()):self.assertEqual(run(['close']),0)
            with patch.dict(os.environ,self.environment('qq-2')):self.assertEqual(run(['open','https://example.com']),0)
            self.assertEqual(execute.call_count,3)

    def test_timeout_keeps_slot_until_cleanup(self):
        import subprocess
        with patch('qqhermes.browser.LEASE',self.root/'lease'),patch.dict(os.environ,self.environment()),patch('qqhermes.browser.subprocess.run',side_effect=subprocess.TimeoutExpired('browser',75)):
            self.assertEqual(run(['click','@e1']),124)
            self.assertTrue(json.loads((self.root/'lease').read_text())['session'])

    def test_expired_lease_is_closed_before_next_owner_and_failure_keeps_slot(self):
        lease=self.root/'lease'
        previous={'chat':'1','session':'old','home':str(self.root/'profiles/qq-1/cache/agent-browser-home'),
                  'persistent':False,'updated':0}
        lease.write_text(json.dumps(previous))
        with patch('qqhermes.browser.LEASE',lease),patch.dict(os.environ,self.environment('qq-2')),patch('qqhermes.browser.subprocess.run') as execute:
            execute.return_value.returncode=1
            self.assertEqual(run(['open','https://example.com']),75)
            self.assertEqual(json.loads(lease.read_text())['chat'],'1')
            execute.return_value.returncode=0
            self.assertEqual(run(['open','https://example.com']),0)
            self.assertEqual(execute.call_args_list[-2].args[0][-1],'close')
            self.assertEqual(execute.call_args_list[-2].kwargs['env']['AGENT_BROWSER_NAMESPACE'],'qq-1')
            self.assertEqual(json.loads(lease.read_text())['chat'],'2')
