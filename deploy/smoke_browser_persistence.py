"""Synthetic login handoff, account retention, and container-restart checks.

Run seed, restart the staging container, then run verify. No real credentials.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

PORT = 18765
BASE = f'http://127.0.0.1:{PORT}'
HTML = '''<!doctype html><meta charset="utf-8"><title>Login fixture</title>
<h1>Scan fixture</h1><p id="status">Waiting for scan</p>
<script>window.tabNonce=crypto.randomUUID();
let timer=setInterval(async()=>{let r=await fetch('/scan-status');let j=await r.json();
if(j.scanned){document.cookie='fixture_login=yes; Max-Age=86400; Path=/';
document.querySelector('#status').textContent='Signed in';clearInterval(timer);}},300);</script>'''

class Handler(BaseHTTPRequestHandler):
    scanned = False
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json' if self.path == '/scan-status' else 'text/html; charset=utf-8')
        self.end_headers()
        if self.path == '/scan-status': data = json.dumps({'scanned': Handler.scanned})
        elif self.path == '/account': data = '<h1>' + ('Signed in' if 'fixture_login=yes' in self.headers.get('Cookie', '') else 'Login required') + '</h1>'
        else: data = HTML
        self.wfile.write(data.encode())
    def log_message(self, *args): pass


def main():
    cfg = json.loads(Path(os.environ['QQ_CONFIG']).read_text())
    assert cfg['bot_qq'] == 99 and cfg['trusted_groups'] == [900001, 900002], 'Synthetic staging only'
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    phase = sys.argv[1] if len(sys.argv) > 1 else 'seed'
    def call(*args, message='turn1', profile='qq-900001', check=True):
        env = dict(os.environ, HERMES_HOME='/opt/data/profiles/' + profile,
                   HERMES_SESSION_ID='fixture-' + message, HERMES_SESSION_MESSAGE_ID=message)
        env.pop('PYTHONPATH', None)
        result = subprocess.run(['agent-browser', *args], env=env, capture_output=True, text=True, timeout=90)
        print(json.dumps({'args': args, 'code': result.returncode, 'output': result.stdout[:600], 'stderr': result.stderr[:300]}), flush=True)
        if check: assert result.returncode == 0, result.stderr + result.stdout
        return result
    try:
        if phase == 'seed':
            call('--account', 'fixture', 'open', BASE + '/login')
            nonce = call('eval', 'window.tabNonce').stdout.splitlines()[1]
            call('login-wait')
            print('WAITING 130s: verifies the old two-minute idle boundary without keeping the browser alive', flush=True)
            time.sleep(130)
            Handler.scanned = True
            time.sleep(1)
            start = time.monotonic()
            assert nonce in call('eval', 'window.tabNonce', message='turn2').stdout
            assert time.monotonic() - start < 10
            assert 'Signed in' in call('get', 'text', '#status', message='turn2').stdout
            call('login-done', message='turn2')
            call('close', message='turn2')
            call('open', BASE + '/account', message='turn3')
            assert 'Signed in' in call('get', 'text', 'body', message='turn3').stdout
            call('close', message='turn3')
            call('--temporary', 'open', BASE + '/account', message='turn4')
            assert 'Login required' in call('get', 'text', 'body', message='turn4').stdout
            call('close', message='turn4')
            call('--account', 'other', 'open', BASE + '/account', profile='qq-900002')
            assert 'Login required' in call('get', 'text', 'body', profile='qq-900002').stdout
            call('close', profile='qq-900002')
            call('--job', 'report', '--account', 'fixture', 'open', BASE + '/account', message='background')
            assert 'Signed in' in call('--job', 'report', 'get', 'text', 'body', message='background').stdout
            assert call('snapshot', message='turn5', check=False).returncode == 75
            call('--job', 'report', 'close', message='background')
            print('SEED PASSED; now restart the isolated container and run verify', flush=True)
        elif phase == 'verify':
            call('open', BASE + '/account', message='after-restart')
            assert 'Signed in' in call('get', 'text', 'body', message='after-restart').stdout
            call('close', message='after-restart')
            call('forget-account', 'fixture', '--confirm', message='after-restart')
            call('--account', 'fixture', 'open', BASE + '/account', message='after-forget')
            assert 'Login required' in call('get', 'text', 'body', message='after-forget').stdout
            call('close', message='after-forget')
            print('RESTART AND EXPLICIT FORGET PASSED', flush=True)
        else: raise ValueError('Expected seed or verify')
    finally:
        call('close', message='cleanup', check=False)
        server.shutdown()

if __name__ == '__main__': main()
