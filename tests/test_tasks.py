import json
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
from types import SimpleNamespace
from qqhermes import tasks


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_patch = patch('qqhermes.tasks.root', return_value=Path(self.tmp.name))
        self.store_patch.start(); self.addCleanup(self.store_patch.stop)
        self.scope = patch('qqhermes.tasks.identity', return_value=(SimpleNamespace(chat_id='1'), 'request', '42'))
        self.scope.start(); self.addCleanup(self.scope.stop)
        self.state = dict(action='update', objective='Show the order screenshot', mode='inspect',
                          target='account engineering, order Y6', constraints='Do not edit; price was zero',
                          verified='Order exists', pending='User checking marking', next_step='Open details and screenshot')

    def test_checkpoint_is_request_scoped_and_does_not_reset_budget(self):
        self.assertIsNotNone(tasks.browser_guard('agent-browser open https://example.test'))
        self.assertTrue(json.loads(tasks.task_tool(self.state))['success'])
        for _ in range(40):
            self.assertIsNone(tasks.browser_guard('agent-browser --account engineering snapshot'))
        self.assertIsNotNone(tasks.browser_guard('agent-browser click @e1'))
        tasks.task_tool(self.state)
        self.assertIsNotNone(tasks.browser_guard('agent-browser click @e1'))
        for action in ('close', 'diagnose', 'checkpoint original'):
            self.assertIsNone(tasks.browser_guard('agent-browser '+action))
        self.assertEqual(tasks.current_checkpoint()['mode'], 'inspect')

    def test_new_turn_cannot_inherit_old_scope_as_permission(self):
        tasks.task_tool(self.state)
        with patch('qqhermes.tasks.identity', return_value=(SimpleNamespace(chat_id='1'), 'next', '42')):
            self.assertIsNone(tasks.current_checkpoint())
            self.assertIsNotNone(tasks.browser_guard('agent-browser click @e1'))
            old = json.loads(tasks.task_tool({'action': 'get', 'message_id': 'request'}))
            self.assertEqual(old['state']['mode'], 'inspect')
        with patch('qqhermes.tasks.identity', return_value=(SimpleNamespace(chat_id='2'), 'request', '42')):
            self.assertIsNone(tasks.current_checkpoint())

    def test_chained_commands_count_and_partial_checkpoint_rejected(self):
        self.assertFalse(json.loads(tasks.task_tool({'action': 'update', 'mode': 'inspect'}))['success'])
        tasks.task_tool(self.state)
        self.assertIsNotNone(tasks.browser_guard(' && '.join(['agent-browser snapshot'] * 41)))
        self.assertIsNone(tasks.browser_guard('agent-browser snapshot'))
