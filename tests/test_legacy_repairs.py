from pathlib import Path
import tempfile
import unittest
from deploy.reconcile_quality import reconcile,repair_memory

class LegacyRepairTests(unittest.TestCase):
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
