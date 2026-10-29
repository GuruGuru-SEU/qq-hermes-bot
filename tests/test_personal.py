"""Exercise private profiles through native Hermes routing, cron and tool policy."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from qqhermes.conversation import conversation, conversation_for_profile
from qqhermes.history import History, current_archive
from qqhermes.policy import before_tool, after_tool
from qqhermes.preferences import fresh_context
from qqhermes.protocol import Gateway
from qqhermes.runtime import safe_write_roots
from qqhermes.store import Store
from qqhermes.tools import history_tool


class PersonalTests(unittest.TestCase):
    def setUp(self):
        from hermes_constants import set_hermes_home_override
        from gateway.session_context import set_session_vars
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.home=self.base/'hermes';self.state=self.base/'state'
        config=self.base/'config.json'
        config.write_text(json.dumps({'bot_qq':99,'trusted_groups':[42],'trusted_users':[42,43]}))
        self.env=patch.dict(os.environ,{'QQ_CONFIG':str(config),'QQ_STATE':str(self.state),
                                      'HERMES_HOME':str(self.home),'DEEPSEEK_API_KEY':'test-not-a-key'})
        self.env.start()
        subprocess.run(['/opt/hermes/.venv/bin/python','/opt/qq-hermes-bot/deploy/bootstrap.py'],check=True,capture_output=True)
        self.profile=self.home/'profiles/qq-dm-42'
        self.override=set_hermes_home_override(self.profile)
        self.session=set_session_vars(platform='qq_onebot',chat_id='dm-42',chat_type='dm',user_id='42',message_id='100')

    def tearDown(self):
        from hermes_constants import reset_hermes_home_override
        from gateway.session_context import clear_session_vars
        clear_session_vars(self.session);reset_hermes_home_override(self.override)
        self.env.stop();self.tmp.cleanup()

    def test_routes_workspaces_and_profile_state_survive_bootstrap(self):
        from gateway.profile_routing import parse_profile_routes,match_profile_route
        config=json.loads((self.home/'config.yaml').read_text())
        routes=parse_profile_routes(config['gateway']['profile_routes'])
        for chat_id,profile in [('42','qq-42'),('dm-42','qq-dm-42'),('dm-43','qq-dm-43')]:
            self.assertEqual(match_profile_route(routes,'qq_onebot',chat_id=chat_id).profile,profile)
        self.assertEqual(conversation_for_profile().key,'dm-42')
        self.assertEqual(current_archive(),self.state/'users/42/history.sqlite3')
        personal=json.loads((self.profile/'config.yaml').read_text())
        self.assertEqual(personal['platforms']['qq_onebot']['home_channel']['chat_id'],'dm-42')
        self.assertEqual(personal['display']['background_process_notifications'],'off')
        self.assertIn(str(self.state/'users/42/workspace'),safe_write_roots().split(os.pathsep))
        self.assertNotIn(str(self.state/'users/42'),safe_write_roots().split(os.pathsep))
        memory=self.profile/'memories/USER.md';memory.write_text('MY_PERSONAL_PREF')
        skill=self.profile/'skills/custom';skill.mkdir();(skill/'SKILL.md').write_text('custom')
        subprocess.run(['/opt/hermes/.venv/bin/python','/opt/qq-hermes-bot/deploy/bootstrap.py'],check=True,capture_output=True)
        self.assertEqual(memory.read_text(),'MY_PERSONAL_PREF')
        from tools.skills_tool import skills_list,skill_view
        listing=skills_list()
        self.assertIn('agent-browser',listing)
        content=skill_view('agent-browser')
        self.assertIn('agent-browser skills get core',content)
        self.assertIn('web_search',content)
        self.assertTrue((skill/'SKILL.md').exists())

    def test_personal_history_and_preferences_never_select_group_or_other_user(self):
        for key,text in [('dm-42','MY_PRIVATE_HISTORY'),('dm-43','OTHER_SECRET'),(42,'GROUP_SECRET')]:
            archive=History(conversation(key).directory/'history.sqlite3')
            archive.save({'message_id':'1','time':1,'user_id':42,'message':[{'type':'text','data':{'text':text}}]})
        (self.profile/'memories/USER.md').write_text('MY_PERSONAL_PREF')
        other=self.home/'profiles/qq-dm-43/memories/USER.md';other.write_text('OTHER_SECRET')
        result=json.loads(history_tool({'action':'recent'}))
        self.assertEqual([m['text'] for m in result['messages']],['MY_PRIVATE_HISTORY'])
        context=fresh_context()['context']
        self.assertIn('MY_PERSONAL_PREF',context);self.assertNotIn('OTHER_SECRET',context)
        self.assertNotIn('GROUP_SECRET',context)

    def test_native_delivery_parser_cron_and_consent_are_scoped(self):
        from hermes_cli.plugins import get_plugin_manager
        from tools.send_message_targets import resolve_send_target
        from cron.scheduler_delivery import _resolve_delivery_target
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        from tools.cronjob_tools import cronjob
        from cron.jobs import list_jobs
        manager=get_plugin_manager()
        try:
            manager.discover_and_load()
            self.assertEqual(resolve_send_target('qq_onebot','dm-42'),('dm-42',None,None))
            self.assertIsNotNone(resolve_send_target('qq_onebot','dm-999')[2])
            self.assertIsNone(before_tool('send_message',{'target':'qq_onebot:dm-42'}))
            for target in ('qq_onebot:42','qq_onebot:dm-43','all'):
                self.assertEqual(before_tool('send_message',{'target':target})['action'],'block')
                self.assertEqual(before_tool('cronjob',{'action':'create','deliver':target})['action'],'block')
                self.assertEqual(before_tool('cronjob',{'action':'update','failure_deliver':target})['action'],'block')
            result=json.loads(cronjob(action='create',name='private-test',prompt='Say CRON_OK',schedule='1h',deliver='origin',paused=True))
            self.assertTrue(result['success'],result)
            jobs=list_jobs(include_disabled=True);self.assertEqual(len(jobs),1)
            job=jobs[0]
            self.assertEqual(job['origin']['chat_id'],'dm-42')
            self.assertEqual(_resolve_delivery_target(job)['chat_id'],'dm-42')
            for other in ('qq-42','qq-dm-43'):
                override=set_hermes_home_override(self.home/'profiles'/other)
                try:self.assertEqual(list_jobs(include_disabled=True),[])
                finally:reset_hermes_home_override(override)
            args={'goal':'compute'}
            blocked=before_tool('delegate_task',args)
            self.assertIn('reply with /approve',blocked['message'])
            store=Store(self.state/'transport.sqlite3')
            with store.db() as db:proposal=db.execute('SELECT id FROM proposals').fetchone()[0]
            for scope,user in [('dm-43','42'),(42,'42'),('dm-42','43')]:
                with self.assertRaises(PermissionError):store.approve(proposal,scope,user,'101')
            store.approve(proposal,'dm-42','42','101')
            with patch('qqhermes.policy.identity',return_value=('dm-42','42','101')):
                self.assertIsNone(before_tool('delegate_task',args))
                after_tool('delegate_task',result={'subagent_ids':['child-dm']})
                self.assertIsNone(before_tool('delegate_task',{'action':'steer','subagent_id':'child-dm'}))
            with patch('qqhermes.policy.identity',return_value=(42,'42','101')):
                self.assertEqual(before_tool('delegate_task',{'action':'stop','subagent_id':'child-dm'})['action'],'block')
        finally:manager.unload()

    def test_history_cli_without_profile_override_uses_private_workspace(self):
        from contextlib import chdir
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        override=set_hermes_home_override(self.home)
        try:
            with chdir(self.state/'users/42/workspace'):
                self.assertEqual(current_archive(),self.state/'users/42/history.sqlite3')
        finally:reset_hermes_home_override(override)

    def test_private_protocol_ids_and_quotes_remain_gateway_scoped(self):
        gateway=Gateway('snowluma')
        self.assertEqual(gateway.response('send_private_msg',{'message_id':123})['message_id'],'snowluma:123')
        params=gateway.params('send_private_msg',{'user_id':42,'message':[{'type':'reply','data':{'id':'snowluma:12'}}]})
        self.assertEqual(params['message'][0]['data']['id'],'12')

