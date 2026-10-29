"""Real Chromium identity checks; disposable container, synthetic local website."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from qqhermes.browser_identity import user_agent


class Handler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        headers = {k.lower(): v for k, v in self.headers.items()}
        type(self).requests.append((self.path, headers))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        body = '<title>Identity fixture</title><body>'
        body += '<script>window.requestHeaders=' + json.dumps(headers) + '</script>'
        if self.path == '/main':
            body += '<a id="popup" href="/popup" target="_blank">Open popup</a><iframe src="/frame"></iframe>'
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix='qq-identity-') as tmp:
        root = Path(tmp); home = root / 'profiles/qq-900001'; home.mkdir(parents=True)
        config = root / 'config.json'
        config.write_text(json.dumps({'trusted_groups': [900001], 'trusted_users': []}))
        env = dict(os.environ, HERMES_HOME=str(home), QQ_CONFIG=str(config), QQ_STATE=str(root/'state'),
                   HERMES_SESSION_ID='identity-fixture', HERMES_SESSION_MESSAGE_ID='1')
        env.pop('PYTHONPATH', None)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://127.0.0.1:{server.server_port}'
        expected = user_agent(); major = expected.split('Chrome/')[1].split('.')[0]

        def call(*args):
            result = subprocess.run(['agent-browser', *args], env=env, capture_output=True, text=True, timeout=90)
            assert result.returncode == 0, (args, result.stdout, result.stderr)
            return result.stdout

        def evaluate(js):
            return json.loads(call('eval', js, '--json'))['data']['result']

        def assert_identity(value):
            assert value['ua'] == expected, value
            assert value['data']['platform'] == 'Linux', value
            assert {'brand': 'Chromium', 'version': major} in value['data']['brands'], value

        def identity_js(target='window'):
            return '({ua:'+target+'.navigator.userAgent,data:'+target+'.navigator.userAgentData.toJSON()})'

        try:
            for mode in (['--temporary'], ['--account', 'test']):
                call(*mode, 'open', origin+'/main')
                call('wait', '--fn', 'document.querySelector("iframe").contentWindow.document.readyState==="complete"')
                assert_identity(evaluate(identity_js()))
                assert_identity(evaluate(identity_js('document.querySelector("iframe").contentWindow')))
                call('click', '#popup')
                # Wait for the popup's actual first HTTP request, not a fixed sleep.
                deadline = time.monotonic()+10
                while not any(path == '/popup' for path, _ in Handler.requests) and time.monotonic() < deadline:
                    time.sleep(.05)
                assert any(path == '/popup' for path, _ in Handler.requests), 'No popup request'
                tabs = json.loads(call('tab', '--json'))['data']['tabs']
                popup = next(tab for tab in tabs if tab['url'] == origin+'/popup')
                call('tab', popup['tabId'])
                call('wait', '--fn', 'location.pathname==="/popup"')
                assert_identity(evaluate(identity_js()))
                call('close')
                if mode[0] == '--account':
                    call(*mode, 'open', origin+'/reopen')
                    assert_identity(evaluate(identity_js()))
                    diagnostic = json.loads(call('diagnose'))
                    assert diagnostic['page']['userAgent'] == expected, diagnostic
                    call('close')
                for path, headers in Handler.requests:
                    assert headers['user-agent'] == expected, (path, headers)
                    assert 'Chromium' in headers.get('sec-ch-ua', ''), (path, headers)
                    assert headers.get('sec-ch-ua-platform') == '"Linux"', (path, headers)
                Handler.requests.clear()
            print('BROWSER IDENTITY PASSED: initial headers, JS, client hints, iframes, popups, temporary/persistent reopen; '+expected, flush=True)
        finally:
            call('close'); server.shutdown()


if __name__ == '__main__':
    main()
