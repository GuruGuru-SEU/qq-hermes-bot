import json
import unittest
from unittest.mock import patch

import httpx
from qqhermes.errors import tool_failure


class ToolErrorTests(unittest.TestCase):
    def test_transient_http_failure_has_retry_metadata_without_signed_url(self):
        request = httpx.Request('GET', 'https://example.com/file?token=private')
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError('private URL and token', request=request, response=response)
        with patch('qqhermes.errors.LOG'):
            result = tool_failure(error, 'download')
        self.assertTrue(result['retryable'])
        self.assertEqual(result['http_status'], 503)
        self.assertEqual(len(result['diagnostic_id']), 12)
        self.assertNotIn('private', json.dumps(result))

    def test_invalid_local_path_is_not_retryable(self):
        with patch('qqhermes.errors.LOG'):
            result = tool_failure(PermissionError('/private/path'), 'upload')
        self.assertFalse(result['retryable'])
        self.assertEqual(result['error'], 'PermissionError')
        self.assertNotIn('/private', json.dumps(result))
