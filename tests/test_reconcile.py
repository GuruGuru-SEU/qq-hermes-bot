import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('reconcile',Path(__file__).resolve().parents[1]/'deploy/reconcile_preferences.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ReconcileTests(unittest.TestCase):
    def test_repairs_only_known_skill_with_backup_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile=root/'profiles/qq-1'
            skill=profile/'skills/qq/qq-group-chat-replies/SKILL.md';skill.parent.mkdir(parents=True)
            original='4. Verify archive queries. It may come back with an unfiltered page\n5. Reply in 1–3 short plain-text sentences with （不是）'
            skill.write_text(original)
            other=profile/'skills/other/SKILL.md';other.parent.mkdir();other.write_text('Untouched')
            changes=module.reconcile(root)
            self.assertEqual(len(changes),1)
            self.assertEqual((Path(changes[0]['backup'])/'SKILL.md').read_text(),original)
            self.assertNotIn('（不是）',skill.read_text())
            self.assertIn('applied_filters',skill.read_text())
            self.assertEqual(other.read_text(),'Untouched')
            self.assertEqual(module.reconcile(root),[])
