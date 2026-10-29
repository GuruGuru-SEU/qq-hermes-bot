from pathlib import Path
import tempfile
import unittest
from qqhermes.runtime_facts import migrate_observations


class RuntimeFactsTests(unittest.TestCase):
    def test_move_only_known_obsolete_runtime_paragraph_with_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            memory = profile/'memories/MEMORY.md'
            memory.parent.mkdir()
            stale = '运行时已为可信会话工作区配置文件写入权限；旧的故障已修复。系统无 sqlite3 CLI、无中文字体（工作区有 NotoSansSC.otf）。原 .venv 已损坏，改用 .venv2。\n'
            preferences = '不要说 xxx。\n头图字体要用粗黑体。\n'
            memory.write_text(preferences + stale)
            self.assertTrue(migrate_observations(profile))
            self.assertEqual(memory.read_text(), preferences)
            self.assertIn(stale, (profile/'runtime-observations.md').read_text())
            backups = list((profile/'backups').glob('*/MEMORY.md'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), preferences + stale)
            self.assertFalse(migrate_observations(profile))
