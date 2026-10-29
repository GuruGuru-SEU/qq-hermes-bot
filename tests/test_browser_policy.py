import unittest
from qqhermes.browser_policy import command_error
from qqhermes.policy import before_tool


class BrowserPolicyTests(unittest.TestCase):
    def test_hiding_errors_and_truncating_browser_output_is_blocked(self):
        for command in ('agent-browser snapshot | tail -10',
                        'cd /tmp && agent-browser --persistent network console 2>&1|grep error',
                        'agent-browser click @e1 >/dev/null 2>&1',
                        '/usr/local/bin/agent-browser open https://example.test &>/dev/null'):
            self.assertIsNotNone(command_error(command),command)
            self.assertEqual(before_tool('terminal',{'command':command})['action'],'block')

    def test_normal_commands_and_unrelated_pipes_are_unchanged(self):
        for command in ('agent-browser diagnose',
                        'agent-browser checkpoint before && agent-browser click @e1',
                        'agent-browser snapshot > snapshot.txt && head snapshot.txt',
                        "agent-browser eval " + chr(39) + 'document.body.innerText.split("|").length' + chr(39),
                        'cat file | grep agent-browser',
                        'python script.py >/dev/null'):
            self.assertIsNone(command_error(command),command)
