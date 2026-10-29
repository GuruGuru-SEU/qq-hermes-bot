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
        self.root=Path(self.tmp.name).resolve();cfg=self.root/'config.json'
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

    def invoke(self, args, profile='qq-1', message='m1'):
        with patch.dict(os.environ, self.environment(profile, message=message)):
            return run(args)

    def test_persistent_identity_survives_new_message_and_job(self):
        _, first, _ = prepare(['--account', 'engineering', 'open', 'https://example.com'], self.environment())
        _, next_turn, _ = prepare(['--account', 'engineering', 'snapshot'], self.environment(message='m2'))
        _, background, owner = prepare(['--job', 'report', '--account', 'engineering', 'snapshot'], self.environment(session='child'))
        self.assertEqual(first['AGENT_BROWSER_SESSION'], next_turn['AGENT_BROWSER_SESSION'])
        self.assertEqual(first['AGENT_BROWSER_PROFILE'], background['AGENT_BROWSER_PROFILE'])
        self.assertEqual(owner['lane'], 'job:report')

    def test_routing_reuses_account_but_keeps_unrelated_sites_temporary(self):
        from qqhermes.browser import read_state
        with patch('qqhermes.browser.LEASE', self.root/'lease'), patch('qqhermes.browser.subprocess.run') as execute:
            execute.return_value.returncode = 0
            self.assertEqual(self.invoke(['--account', 'engineering', 'open', 'https://example.com/dashboard?token=secret']), 0)
            self.assertEqual(self.invoke(['close']), 0)
            self.assertEqual(self.invoke(['open', 'https://example.com/projects'], message='m2'), 0)
            self.assertEqual(execute.call_args.kwargs['env']['AGENT_BROWSER_PROFILE'], str(self.root/'profiles/qq-1/browser-profiles/engineering'))
            self.assertEqual(self.invoke(['open', 'https://unrelated.example/'], message='m3'), 0)
            self.assertNotIn('AGENT_BROWSER_PROFILE', execute.call_args.kwargs['env'])
            state = read_state(self.root/'profiles/qq-1')
            self.assertNotIn('secret', json.dumps(state))
            self.assertEqual(state['origins'], {'https://example.com': 'engineering'})

    def test_qr_handoff_same_session_next_turn_no_sleep_or_navigation(self):
        with patch('qqhermes.browser.LEASE', self.root/'lease'), patch('qqhermes.browser.subprocess.run') as execute:
            execute.return_value.returncode = 0
            execute.return_value.stdout = 'https://login.example/qr?secret=hidden\n'
            self.invoke(['--account', 'engineering', 'open', 'https://example.com/login'])
            original = execute.call_args.kwargs['env']['AGENT_BROWSER_SESSION']
            self.assertEqual(self.invoke(['login-wait']), 0)
            self.assertEqual(self.invoke(['snapshot', '-i'], message='m2'), 0)
            self.assertEqual(execute.call_args.args[0][-2:], ['snapshot', '-i'])
            self.assertEqual(execute.call_args.kwargs['env']['AGENT_BROWSER_SESSION'], original)
            count = execute.call_count
            self.assertEqual(self.invoke(['--temporary', 'open', 'https://elsewhere.example/'], message='m2'), 75)
            self.assertEqual(execute.call_count, count)
            self.assertEqual(self.invoke(['login-done'], message='m2'), 0)
            self.assertEqual(self.invoke(['close'], message='m2'), 0)
            self.assertEqual((self.root/'lease').read_text(), '')

    def test_account_can_switch_immediately_without_erasing_cookies(self):
        with patch('qqhermes.browser.LEASE', self.root/'lease'), patch('qqhermes.browser.subprocess.run') as execute:
            execute.return_value.returncode = 0
            self.invoke(['--account', 'first', 'open', 'https://example.com'])
            cookie = self.root/'profiles/qq-1/browser-profiles/first/cookie-fixture'
            cookie.write_text('retained')
            self.assertEqual(self.invoke(['--account', 'second', 'open', 'https://other.example'], message='m2'), 0)
            self.assertEqual(execute.call_args_list[-2].args[0][-1], 'close')
            self.assertEqual(cookie.read_text(), 'retained')

    def test_same_account_background_cannot_steal_foreground(self):
        with patch('qqhermes.browser.LEASE', self.root/'lease'), patch('qqhermes.browser.subprocess.run') as execute:
            execute.return_value.returncode = 0
            self.invoke(['--account', 'engineering', 'open', 'https://example.com'])
            self.assertEqual(self.invoke(['--job', 'report', '--account', 'engineering', 'snapshot']), 75)
            self.assertEqual(execute.call_count, 1)

    def test_forgetting_closed_account_preserves_unrelated_live_lease(self):
        with patch('qqhermes.browser.LEASE', self.root/'lease'), patch('qqhermes.browser.subprocess.run') as execute:
            execute.return_value.returncode = 0
            self.invoke(['--account', 'engineering', 'open', 'https://example.com'])
            self.invoke(['close'])
            self.invoke(['open', 'about:blank'], profile='qq-2')
            lease = (self.root/'lease').read_text()
            count = execute.call_count
            self.assertEqual(self.invoke(['forget-account', 'engineering', '--confirm']), 0)
            self.assertEqual(execute.call_count, count)
            self.assertEqual((self.root/'lease').read_text(), lease)
            self.assertFalse((self.root/'profiles/qq-1/browser-profiles/engineering').exists())

    def test_migrates_existing_profile_origins_without_copying_login_data(self):
        import sqlite3
        from qqhermes.browser import read_state
        home = self.root/'profiles/qq-1'
        old = home/'browser-profiles/personal/Default'; old.mkdir(parents=True)
        with sqlite3.connect(old/'History') as db:
            db.execute('CREATE TABLE urls(url TEXT,last_visit_time INTEGER)')
            db.execute('INSERT INTO urls VALUES (?,?)', ('https://example.com/login?code=private', 1))
        state = read_state(home)
        self.assertEqual(state['origins'], {'https://example.com': 'personal'})
        self.assertNotIn('private', json.dumps(state))
        _, env, _ = prepare(['open', 'https://example.com/projects'], self.environment())
        self.assertEqual(env['AGENT_BROWSER_PROFILE'], str(home/'browser-profiles/personal'))

    def test_mode_validation_and_temporary_login_wait(self):
        for args in (['--account', '../foreign', 'open', 'x'], ['--temporary', '--persistent', 'close'],
                     ['forget-account', 'engineering'], ['--account', 'x', '--temporary', 'close']):
            with self.assertRaises(ValueError): prepare(args, self.environment())
        with patch('qqhermes.browser.LEASE', self.root/'lease'):
            with self.assertRaises(ValueError): self.invoke(['login-wait'])
