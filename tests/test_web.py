import json
import unittest
from unittest.mock import AsyncMock, patch

from qqhermes.web import DeepSeekWeb


class NativeWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_extract_accepts_provider_response(self):
        from tools.web_tools import web_extract_tool
        provider = DeepSeekWeb()
        content = 'Useful extracted page content. ' * 20
        with patch.object(provider, 'request', return_value={'title':'Example', 'content':content}), \
                patch('tools.web_tools.async_is_safe_url', AsyncMock(return_value=True)), \
                patch('tools.web_tools._ensure_web_plugins_loaded'), \
                patch('tools.web_tools._resolve_extract_provider', return_value=(provider, None)):
            result = json.loads(await web_extract_tool(['https://example.com/']))
        self.assertEqual(len(result['results']), 1)
        self.assertIn('Useful extracted page content', result['results'][0]['content'])

    def test_failed_fetch_is_per_url(self):
        provider = DeepSeekWeb()
        with patch.object(provider, 'request', side_effect=[RuntimeError(), {'content':'second page'}]):
            result = provider.extract(['https://example.com/one', 'https://example.com/two'])
        self.assertEqual(result[0]['error'], 'RuntimeError')
        self.assertEqual(result[1]['content'], 'second page')


if __name__ == '__main__':
    unittest.main()
