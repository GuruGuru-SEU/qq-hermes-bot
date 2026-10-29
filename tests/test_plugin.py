"""Load the real plugin through Hermes discovery, including routed-profile hooks."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class DiscoveryTests(unittest.TestCase):
    def test_routed_profile_enforces_consent_and_records_native_child(self):
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        from gateway.session_context import set_session_vars,clear_session_vars
        from hermes_cli.plugins import get_plugin_manager,invoke_hook
        from qqhermes.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);home=root/'profiles/qq-1';home.mkdir(parents=True)
            plugin=home/'plugins/qq-hermes-bot';plugin.parent.mkdir()
            import qqhermes
            plugin.symlink_to(Path(qqhermes.__file__).resolve().parents[1],target_is_directory=True)
            (home/'config.yaml').write_text(json.dumps({'plugins':{'enabled':['qq-hermes-bot']},'platforms':{'qq_onebot':{'enabled':False}}}))
            config=root/'config.json';config.write_text(json.dumps({'bot_qq':99,'trusted_groups':[1]}))
            with patch.dict(os.environ,{'QQ_STATE':str(root/'state'),'QQ_CONFIG':str(config),'ONEBOT_TOKEN':'test-token'}):
                override=set_hermes_home_override(home)
                session=set_session_vars(user_id='42',message_id='100',chat_id='1',platform='qq_onebot')
                manager=get_plugin_manager()
                try:
                    manager.discover_and_load()
                    from hermes_cli.middleware import apply_llm_request_middleware
                    probe={'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,broken...'}}]}]}
                    prepared=apply_llm_request_middleware(probe,base_url='https://api.deepseek.com',model='deepseek-flash')
                    self.assertTrue(prepared.changed)
                    self.assertEqual(prepared.payload['messages'][0]['content'][0]['type'],'text')
                    # Final replies pass through without an extra model reviewer.
                    from hermes_cli.plugins import iter_hook_callbacks
                    callbacks=list(iter_hook_callbacks('transform_llm_output'))
                    self.assertFalse(any(c.__name__=='review_output' for c in callbacks))
                    self.assertFalse((Path(qqhermes.__file__).parent/'quality.py').exists())
                    malformed={'calls':[{'name':'qq_history','action':'search','contains':'needle'}]}
                    blocked=invoke_hook('pre_tool_call',tool_name='tool_call',args=malformed)
                    self.assertEqual(blocked[0]['action'],'block')
                    self.assertIn('arguments',blocked[0]['message'])
                    self.assertTrue(invoke_hook('pre_tool_call',tool_name='qq_history',args={}))
                    from model_tools import handle_function_call
                    rejected=handle_function_call('tool_call',malformed)
                    self.assertIn('missing required argument(s): action',str(rejected))
                    failed=handle_function_call('qq_history',{})
                    self.assertIn('requires explicit action',str(failed))
                    store=Store(root/'state/transport.sqlite3')
                    with store.db() as db:db.execute('INSERT INTO members VALUES (1,?,?,0)',('42','Short test replies'))
                    context=invoke_hook('pre_llm_call',user_message='test')
                    self.assertIn('Short test replies',context[0]['context'])
                    args={'goal':'calculate 27*31'}
                    result=invoke_hook('pre_tool_call',tool_name='delegate_task',args=args)
                    self.assertEqual(result[0]['action'],'block')
                    store=Store(root/'state/transport.sqlite3')
                    with store.db() as db:ident=db.execute('SELECT id FROM proposals').fetchone()[0]
                    store.approve(ident,1,'42','101')
                    clear_session_vars(session)
                    session=set_session_vars(user_id='42',message_id='101',chat_id='1',platform='qq_onebot')
                    self.assertEqual(invoke_hook('pre_tool_call',tool_name='delegate_task',args=args),[])
                    invoke_hook('post_tool_call',tool_name='delegate_task',args=args,result=json.dumps({'subagent_ids':['child-1']}))
                    self.assertEqual(invoke_hook('pre_tool_call',tool_name='delegate_task',args={'action':'steer','subagent_id':'child-1','message':'use C++'}),[])
                    with store.db() as db:self.assertEqual(db.execute('SELECT user_id FROM children').fetchone()[0],'42')
                finally:
                    manager.unload()
                    clear_session_vars(session)
                    reset_hermes_home_override(override)


if __name__=='__main__':unittest.main()
