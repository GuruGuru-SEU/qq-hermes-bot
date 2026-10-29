import unittest

from qqhermes.browser_identity import chrome_user_agent


class BrowserIdentityTests(unittest.TestCase):
    def test_version_tracks_installed_chromium_with_reduced_minor_version(self):
        for major in (156, 157):
            ua = chrome_user_agent(f'Chromium {major}.0.8078.73 built on Debian GNU/Linux 13 (trixie)\n')
            self.assertIn(f'Chrome/{major}.0.0.0', ua)
            self.assertIn('Linux x86_64', ua)
            self.assertNotIn('HeadlessChrome', ua)

    def test_unknown_versions_do_not_fall_back_to_stale_identity(self):
        for value in ('', 'error', 'Chromium invalid', 'Chromium 156'):
            with self.assertRaises(ValueError):chrome_user_agent(value)
