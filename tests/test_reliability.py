import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from qqhermes.validation import wrapper_error,bounded_history,validate_history
from qqhermes.tools import history_tool
from qqhermes.history import History
from qqhermes.store import Store


class HistoryValidationTests(unittest.TestCase):
    def test_malformed_wrapper_never_becomes_unfiltered_query(self):
        flattened={'calls':[{'name':'qq_history','action':'search','contains':'needle'}]}
        self.assertIn('arguments',wrapper_error('tool_call',flattened))
        self.assertIn('arguments',wrapper_error('tool_call',{'name':'qq_history','action':'stats'}))
        valid={'calls':[{'name':'qq_history','arguments':{'action':'search','contains':'needle'}}]}
        self.assertIsNone(wrapper_error('tool_call',valid))
        with patch('qqhermes.tools.query') as query:
            self.assertIn('error',json.loads(history_tool({})))
            self.assertIn('error',json.loads(history_tool({'action':'search'})))
            query.assert_not_called()

    def test_unknown_or_ignored_filters_are_rejected(self):
        for args in ({'action':'unknown'}, {'action':'search','contains':'x','typo':1},
                     {'action':'search','members':True},{'action':'stats','context':2},
                     {'action':'search','limit':True}):
            with self.subTest(args=args),self.assertRaises(ValueError):validate_history(args)

    def test_large_attachment_metadata_and_removed_context_are_flagged(self):
        result=bounded_history({'messages':[{'message_id':'1','text':'hello','files':[{'name':'x'*2000}]*100,
                                             'context_ids':['2']}],
                                'context_messages':[{'message_id':'2','text':'x'*20000}],
                                'total':1,'notice':'Archive may have gaps.'},
                               {'action':'search','message_id':'1'})
        self.assertLess(len(json.dumps(result)),12000)
        self.assertEqual(result['messages'][0]['files_omitted'],100)
        self.assertEqual(result['messages'][0]['context_ids'],[])
        self.assertTrue(result['context_truncated'])
        self.assertIn('Archive may have gaps',result['notice'])

    def test_real_history_filters_and_bounded_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'groups/1/history.sqlite3';archive=History(path)
            for i in range(50):
                archive.save({'message_id':str(i),'time':i,'user_id':42,'sender':{'nickname':'Test'},
                              'message':[{'type':'text','data':{'text':('needle ' if i%2 else 'other ')+('x'*1900)}}]})
            with patch('qqhermes.tools.group_for_profile',return_value=1),patch('qqhermes.tools.root',return_value=Path(tmp)):
                result=json.loads(history_tool({'action':'search','contains':'needle','limit':50}))
            self.assertEqual(result['total'],25)
            self.assertTrue(all('needle' in m['text'] for m in result['messages']))
            self.assertLess(len(json.dumps(result,ensure_ascii=False)),14000)
            self.assertEqual(result['next_offset'],len(result['messages']))
            self.assertEqual(result['applied_filters']['contains'],'needle')
            self.assertEqual(result['selection']['returned'],len(result['messages']))
            self.assertTrue(result['response_truncated'])


class PreferenceTests(unittest.TestCase):
    def test_latest_memory_and_only_current_speaker_are_loaded(self):
        from qqhermes.preferences import fresh_context,skill_conflict
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        from gateway.session_context import set_session_vars,clear_session_vars
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);home=root/'profiles/qq-1';(home/'memories').mkdir(parents=True)
            config=root/'config.json';config.write_text(json.dumps({'trusted_groups':[1,2]}))
            memory=home/'memories/MEMORY.md';memory.write_text('Use short replies.')
            with patch.dict(os.environ,{'QQ_CONFIG':str(config),'QQ_STATE':str(root/'state')}):
                store=Store(root/'state/transport.sqlite3')
                with store.db() as db:
                    db.executemany('INSERT INTO members VALUES (?,?,?,0)',[(1,'42','Call me Test'),(1,'43','OTHER_MEMBER_SECRET'),(2,'42','OTHER_GROUP_SECRET')])
                override=set_hermes_home_override(home)
                session=set_session_vars(user_id='42',message_id='1',chat_id='1',platform='qq_onebot')
                try:
                    value=fresh_context()['context']
                    self.assertIn('Call me Test',value)
                    self.assertNotIn('OTHER_',value)
                    memory.write_text('禁止在回复末尾加“TEST_END”。')
                    self.assertIn('TEST_END',fresh_context()['context'])
                    self.assertIsNotNone(skill_conflict({'action':'patch','new_string':'Finish with TEST_END'}))
                    self.assertIsNone(skill_conflict({'action':'patch','old_string':'TEST_END','new_string':'Follow current preferences'}))
                finally:
                    clear_session_vars(session);reset_hermes_home_override(override)


if __name__=='__main__':unittest.main()
