from qqhermes.conversation import Conversation
import json
import os
import unittest
from unittest.mock import patch

import httpx
from gateway.platforms.base import SendResult
from qqhermes.outbound import send_result,http_send_result
from qqhermes.tools import send_tool


class OutboundTests(unittest.TestCase):
    def test_success_is_not_marked_failed_by_native_hermes(self):
        from agent.display import _detect_tool_failure
        from agent.tool_guardrails import classify_tool_failure
        old=json.dumps({'success':True,'message_id':None,'error':None})
        # Hermes 0.22.5 fixes the historical error:null false failure.
        self.assertFalse(classify_tool_failure('qq_send',old)[0])
        for kind in ('file','image','text'):
            value=send_result(SendResult(success=True),kind)
            wire=json.dumps(value)
            self.assertNotIn('error',value)
            self.assertNotIn('message_id',value)
            self.assertFalse(_detect_tool_failure('qq_send',wire)[0])
            self.assertFalse(classify_tool_failure('qq_send',wire)[0])
        self.assertEqual(send_result(SendResult(success=True),'file')['delivery'],'uploaded')

    def test_real_failure_remains_a_failure(self):
        from agent.tool_guardrails import classify_tool_failure
        value=send_result(SendResult(success=False,error='TimeoutError',error_kind='delivery_unknown'),'file')
        self.assertFalse(value['success'])
        self.assertTrue(classify_tool_failure('qq_send',json.dumps(value))[0])
        self.assertFalse(value['retryable'])

    def test_rate_limit_details_survive_http_boundary(self):
        payload={'success':False,'error':'Local group send rate limit','error_kind':'rate_limited',
                 'retry_after':37,'delivery':'not_attempted'}
        response=httpx.Response(429,json=payload)
        with patch('httpx.post',return_value=response) as post,patch('qqhermes.tools.conversation_for_profile',return_value=Conversation('group',1)),patch.dict(os.environ,{'ONEBOT_TOKEN':'fixture'}):
            result=json.loads(send_tool({'kind':'file','reason':'requested','path':'/fixture.txt'}))
        self.assertEqual(result['error_kind'],'rate_limited')
        self.assertEqual(result['retry_after'],37)
        self.assertEqual(result['http_status'],429)
        self.assertEqual(result['delivery'],'not_attempted')
        post.assert_called_once()

    def test_old_endpoint_success_is_normalized(self):
        response=httpx.Response(200,json={'success':True,'message_id':None,'error':None})
        self.assertEqual(http_send_result(response),{'success':True})
