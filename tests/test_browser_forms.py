import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from qqhermes.browser_forms import execute, replace_text, FormError

class BrowserFormTests(unittest.TestCase):
    def test_missing_upload_fails_before_any_browser_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, patch('qqhermes.browser_forms.subprocess.run') as run:
            good=Path(tmp)/'image.png';good.write_bytes(b'fixture')
            with self.assertRaises(FormError):
                execute('browser',{},['upload','#file',str(good),str(Path(tmp)/'missing.png')])
            run.assert_not_called()

    def test_clear_failure_does_not_insert_or_report_success(self):
        with patch('qqhermes.browser_forms.native') as call, patch('qqhermes.browser_forms.evaluate') as evaluate, patch('qqhermes.browser_forms.command_data', return_value={'count':1,'value':'1'}):
            evaluate.side_effect=[{'editable':True,'kind':'div'},{'matches':False},True]
            with self.assertRaisesRegex(FormError,'did not clear'):
                replace_text('browser',{},'#editor','new text')
            self.assertFalse(any(c.args[2][0]=='keyboard' for c in call.call_args_list))

    def test_reverted_input_fails_verification(self):
        with patch('qqhermes.browser_forms.native'), patch('qqhermes.browser_forms.evaluate') as evaluate, patch('qqhermes.browser_forms.command_data', return_value={'count':1,'value':'1'}):
            evaluate.side_effect=[{'editable':True},{'matches':True},{'matches':True},{'matches':False},True]
            with self.assertRaisesRegex(FormError,'reverted'):
                replace_text('browser',{},'#editor','new text')

    def test_file_preserves_newlines_and_shell_metacharacters(self):
        text='第一行 👀\n"quotes" $HOME $(uname) '+chr(96)+'id'+chr(96)+'\n'
        with tempfile.TemporaryDirectory() as tmp, patch('qqhermes.browser_forms.replace_text',return_value={'success':True}) as replace:
            path=Path(tmp)/'body.txt';path.write_text(text)
            with contextlib.redirect_stdout(io.StringIO()):
                execute('browser',{},['replace-text','#editor','--file',str(path)])
            self.assertEqual(replace.call_args.args[3],text)

    def test_invalid_file_encoding_and_size_fail_before_edit(self):
        with tempfile.TemporaryDirectory() as tmp, patch('qqhermes.browser_forms.replace_text') as replace:
            path=Path(tmp)/'body.txt';path.write_bytes(b'\xff')
            with self.assertRaises(UnicodeDecodeError):
                execute('browser',{},['replace-text','#editor','--file',str(path)])
            path.write_bytes(b'x'*100001)
            with self.assertRaises(FormError):
                execute('browser',{},['replace-text','#editor','--file',str(path)])
            replace.assert_not_called()

    def test_failed_upload_keeps_native_error_exit(self):
        with tempfile.TemporaryDirectory() as tmp, patch('qqhermes.browser_forms.subprocess.run',return_value=subprocess.CompletedProcess([],7)):
            path=Path(tmp)/'image.png';path.write_bytes(b'fixture')
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                result=execute('browser',{},['upload','#file',str(path)])
            self.assertEqual(result.returncode,7)
            self.assertNotIn('files_selected',output.getvalue())
