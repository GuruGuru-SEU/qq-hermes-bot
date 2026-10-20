import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqhermes.runtime import safe_write_roots


class WriteRootTests(unittest.TestCase):
    def test_workspaces_allowed_but_archive_and_untrusted_paths_denied(self):
        from agent.file_safety import get_write_denied_error
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);home=base/'hermes';home.mkdir()
            config=base/'config.json';config.write_text(json.dumps({'trusted_groups':[1,2]}))
            with patch.dict(os.environ,{'QQ_CONFIG':str(config),'QQ_STATE':str(base/'state'),
                                        'HERMES_HOME':str(home),'HERMES_WRITE_SAFE_ROOT':str(home)}):
                allowed=safe_write_roots()
                self.assertEqual(len(allowed.split(os.pathsep)),3)
                override=set_hermes_home_override(home)
                try:
                    with patch.dict(os.environ,{'HERMES_WRITE_SAFE_ROOT':allowed}):
                        for group in (1,2):
                            path=base/f'state/groups/{group}/workspace/render.py'
                            path.parent.mkdir(parents=True)
                            self.assertIsNone(get_write_denied_error(str(path)))
                        for path in (base/'state/transport.sqlite3',base/'state/groups/1/history.sqlite3',
                                     base/'state/groups/3/workspace/file.py',home/'state.db',home/'.env'):
                            self.assertIsNotNone(get_write_denied_error(str(path)))
                        link=base/'state/groups/1/workspace/escape';link.symlink_to(base/'state')
                        self.assertIsNotNone(get_write_denied_error(str(link/'transport.sqlite3')))
                finally:reset_hermes_home_override(override)
