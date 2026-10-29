import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qqhermes import browser_recovery as recovery
from qqhermes.browser_forms import FormError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env={'HOME':self.tmp.name,'AGENT_BROWSER_SESSION':'account-one'}

    def test_exact_url_kept_private_and_replayed_without_reconstruction(self):
        url='https://example.test/order?fileId=123456789&token=private#/eda?source=oneclick'
        with patch.object(recovery,'command_data',return_value={'url':url}) as call:
            result=recovery.checkpoint('browser',self.env,'handoff',30)
            self.assertNotIn('private',json.dumps(result).replace('privately',''))
            path=recovery.checkpoint_path(self.env,'handoff')
            self.assertEqual(json.loads(path.read_text())['url'],url)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            call.return_value={'url':'https://example.test/other'}
            recovery.resume('browser',self.env,'handoff',30)
            self.assertEqual(call.call_args.args[2],['open',url])
            call.return_value={'url':url}
            recovery.resume('browser',self.env,'handoff',30)
            self.assertEqual(call.call_args.args[2],['reload'])

    def test_missing_foreign_expired_or_invalid_checkpoint_never_navigates(self):
        with patch.object(recovery,'command_data',return_value={'url':'https://example.test/'}) as call:
            recovery.checkpoint('browser',self.env,'old',30)
            path=recovery.checkpoint_path(self.env,'old')
            for fields in ({'saved_at':0},{'session':'another'},{'url':'javascript:alert(1)'}):
                payload={'url':'https://example.test','saved_at':recovery.time.time(),'session':'account-one',**fields}
                path.write_text(json.dumps(payload));call.reset_mock()
                with self.assertRaises(FormError):recovery.resume('browser',self.env,'old',30)
                call.assert_not_called()
            with self.assertRaises(FormError):
                recovery.resume('browser',{**self.env,'AGENT_BROWSER_SESSION':'other'},'old',30)
            with self.assertRaises(FormError):recovery.checkpoint_path(self.env,'../foreign')

    def test_diagnostics_are_bounded_redacted_and_never_navigate(self):
        def command(binary,env,args,deadline):
            if args==['errors']:return {'errors':[{'text':'TypeError failed https://x.test/a?token=secret','line':2,'url':None}]*20}
            if args==['console']:return {'messages':[{'type':'log','text':'ignore'}, {'type':'error','text':'Authorization: Bearer demo-auth-secret'}]}
            if args==['network','requests']:
                return {'requests':[{'requestId':str(i),'resourceType':'XHR','status':500,'url':'https://x.test/api?uuid=hidden-value','headers':{'Cookie':'hidden-value'},'postData':'hidden-value'} for i in range(100)]}
            self.fail(args)
        with patch.object(recovery,'evaluate',return_value={'url':'https://user:pass@x.test/path?key=hidden-value','title':'test','ready':'complete','visibleCharacters':3}),patch.object(recovery,'command_data',side_effect=command):
            result=recovery.diagnose('browser',self.env,30)
        data=json.dumps(result)
        for secret in ('demo-auth-secret','hidden-value','user:pass','token=secret'):self.assertNotIn(secret,data)
        self.assertEqual(len(result['errors']),8)
        self.assertEqual(len(result['recent_requests']),12)
        self.assertEqual(len(result['failed_requests']),8)
        self.assertEqual(result['page']['visibleCharacters'],3)

    def test_partial_diagnostic_failure_is_visible_not_false_clean_bill(self):
        with patch.object(recovery,'evaluate',side_effect=FormError()),patch.object(recovery,'command_data',side_effect=FormError()):
            result=recovery.diagnose('browser',self.env,30)
        self.assertEqual(result['collection_errors'],['page','errors','console','network'])
