import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from qqhermes import quality
from deploy.reconcile_quality import reconcile,repair_memory,repair_skill

class QualityTests(unittest.TestCase):
    def setUp(self):
        quality._TURNS.clear()
        self.scope=patch('qqhermes.conversation.conversation_for_profile',return_value=SimpleNamespace(profile='qq-test',chat_id='test'))
        self.scope.start();self.addCleanup(self.scope.stop)

    def remember(self,question='统计历史消息总数',extra=(),**kwargs):
        request={'model':'deepseek-flash','messages':[{'role':'user','content':'context\n[New message]\n'+question},*extra]}
        quality.remember_request(request,session_id='s',turn_id='t',base_url='https://api.deepseek.com/v1',**kwargs)
        return request

    def test_routes_banter_and_critical_work_differently(self):
        self.assertFalse(quality.review_reasons('你好呀','你好'))
        self.assertTrue(quality.review_reasons('这份保单是否赔付'))
        self.assertTrue(quality.review_reasons('请计算胜率'))
        self.assertTrue(quality.review_reasons('这不是bo5吗'))
        self.assertFalse(quality.review_reasons('讲个笑话'))

    def test_applies_review_once_to_same_turn(self):
        original=self.remember();saved=json.dumps(original)
        with patch.object(quality,'request_review',return_value={'change':True,'issues':['unsupported_majority'],'answer':'没被纠正不代表正确。我们还是应该反思自己的态度。'}) as review:
            answer=quality.review_output('多数消息没有被纠正，但没被纠正不代表正确。我们还是应该反思自己的态度。',session_id='s',turn_id='t')
            self.assertNotIn('多数',answer)
            self.assertIsNone(quality.review_output('another draft',session_id='s',turn_id='t'))
            self.assertEqual(review.call_count,1)
        self.assertEqual(json.dumps(original),saved)

    def test_speaker_is_not_the_question(self):
        self.assertEqual(quality.task_text('context\n[New message]\n[不做学科交叉的学生应该立刻退学] 你认真想 中英混合'),'你认真想 中英混合')

    def test_rejects_topic_drift_and_unfounded_new_numbers(self):
        draft='按你提供的三胜两负记录，胜率是60%。这个结果只包含已列出的五场比赛。'
        self.assertIsNone(quality.accepted_revision({'change':True,'issues':['x'],'answer':'跨学科研究应该得到鼓励，但不参加跨学科研究的学生不应该退学。'},draft,{}))
        self.assertIsNone(quality.accepted_revision({'change':True,'issues':['x'],'answer':draft.replace('60%','80%')},draft,{}))

    def test_cross_profile_does_not_receive_evidence(self):
        self.remember()
        with patch('qqhermes.conversation.conversation_for_profile',return_value=SimpleNamespace(profile='qq-other',chat_id='other')),patch.object(quality,'request_review') as review:
            self.assertIsNone(quality.review_output('x'*60,session_id='s',turn_id='t'));review.assert_not_called()

    def test_timeout_or_invalid_output_preserves_draft(self):
        for value in [TimeoutError(),{'change':True,'issues':[],'answer':'bad'}, {'change':True,'issues':['x'],'answer':'see https://invented.example/x'}]:
            self.remember()
            opts={'side_effect':value} if isinstance(value,Exception) else {'return_value':value}
            with patch.object(quality,'request_review',**opts):
                self.assertIsNone(quality.review_output('x'*60,session_id='s',turn_id='t'))

    def test_text_reviewer_does_not_contradict_unseen_images(self):
        self.remember(extra=[{'role':'tool','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,test'}}]}])
        with patch.object(quality,'request_review') as review:
            self.assertIsNone(quality.review_output('x'*60,session_id='s',turn_id='t'));review.assert_not_called()

    def test_evidence_is_bounded_and_preserves_tool_failures(self):
        self.remember(extra=[{'role':'tool','tool_name':'web_search','content':'x'*20000},{'role':'tool','tool_name':'qq_send','content':'{"success":false,"delivery":"not_attempted"}'}])
        snapshot=next(iter(quality._TURNS.values()))
        self.assertTrue(snapshot['researched']);self.assertLess(len(json.dumps(snapshot)),15000)
        self.assertIn('not_attempted',snapshot['evidence'][-1]['result'])

    def test_migration_preserves_tone_and_unrelated_preferences(self):
        old='本群叫成员甲某称呼。§\n腔调要求：非常串。§\n图片附件先到为 pending；等下一轮再读。'
        new=repair_memory(old)
        self.assertIn('非常串',new);self.assertIn('成员甲',new);self.assertNotIn('等下一轮再读',new)
        self.assertEqual(repair_memory(new),new)

    def test_migration_is_dry_run_by_default_and_backed_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profiles/qq-1/memories/MEMORY.md';path.parent.mkdir(parents=True);path.write_text('keep this preference')
            self.assertTrue(reconcile(tmp));self.assertEqual(path.read_text(),'keep this preference')
            changes=reconcile(tmp,True);backup=Path(changes[0]['backup'])/'MEMORY.md'
            self.assertEqual(backup.read_text(),'keep this preference');self.assertFalse(reconcile(tmp,True))
