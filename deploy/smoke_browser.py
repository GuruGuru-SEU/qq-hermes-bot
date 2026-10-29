"""Exercise Chromium/CLI in an isolated container with no QQ connection."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

from PIL import Image

HTML='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>QQ browser fixture</title>
<h1>浏览器测试</h1><label>名字 <input aria-label="名字" id="name"></label>
<button onclick="document.querySelector('#result').textContent='你好，'+document.querySelector('#name').value">计算</button>
<p id="result">等待操作</p><a href="/download" download="fixture.txt">下载文件</a>
<script>document.body.dataset.ready='yes';localStorage.setItem('test-marker','isolated');</script></html>'''
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        content=('DOWNLOAD_OK\n' if self.path=='/download' else HTML).encode()
        self.send_response(200);self.send_header('Content-Type','text/plain' if self.path=='/download' else 'text/html; charset=utf-8')
        if self.path=='/download':self.send_header('Content-Disposition','attachment; filename=fixture.txt')
        self.end_headers();self.wfile.write(content)
    def log_message(self,*a):pass


def main():
    assert os.environ.get('HERMES_SESSION_CHAT_ID')=='900001','Synthetic staging only'
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    def call(*args,env=None,check=True):
        child_env=dict(os.environ if env is None else env);child_env.pop('PYTHONPATH',None)
        p=subprocess.run(['agent-browser',*args],env=child_env,text=True,capture_output=True,timeout=90)
        print(json.dumps({'args':list(args),'code':p.returncode,'stdout':p.stdout[:450],'stderr':p.stderr[:350]},ensure_ascii=False),flush=True)
        if check:assert p.returncode==0,(args,p.stdout,p.stderr)
        return p
    try:
        call('--version');core=call('skills','get','core');assert 'snapshot' in core.stdout
        call('open',f'http://127.0.0.1:{server.server_port}/')
        snap=call('snapshot','-i');assert '名字' in snap.stdout and '计算' in snap.stdout
        call('fill','#name','Hermes')
        call('click','button')
        result=call('get','text','#result');assert '你好，Hermes' in result.stdout
        screenshot=Path('/state/groups/900001/workspace/browser-screenshots/test.png')
        call('screenshot',str(screenshot));assert Image.open(screenshot).size[0]>0
        other=dict(os.environ,HERMES_HOME='/opt/data/profiles/qq-900002',HERMES_SESSION_ID='other')
        assert call('open','about:blank',env=other,check=False).returncode==75
        call('click','a')
        download=Path('/state/groups/900001/workspace/browser-downloads/fixture.txt')
        import time
        for _ in range(30):
            if download.exists():break
            time.sleep(.1)
        assert download.read_text()=='DOWNLOAD_OK\n'
        call('close')
        call('open',f'http://127.0.0.1:{server.server_port}/',env=other)
        call('close',env=other)
        call('open','https://example.com')
        assert 'Example Domain' in call('get','title').stdout
        call('close')
        url=f'http://127.0.0.1:{server.server_port}/'
        call('--persistent','open',url)
        call('--persistent','eval',"localStorage.setItem('login-fixture','profile-1')")
        call('--persistent','close')
        call('--persistent','open',url)
        assert 'profile-1' in call('--persistent','eval',"localStorage.getItem('login-fixture')").stdout
        call('--persistent','close')
        call('--persistent','open',url,env=other)
        assert 'profile-1' not in call('--persistent','eval',"localStorage.getItem('login-fixture')",env=other).stdout
        call('--persistent','close',env=other)
        print('BROWSER SMOKE PASSED: real Chromium, JS, form/click, Chinese text, screenshot, download, scoped lease, HTTPS',flush=True)
    finally:
        call('close',check=False);server.shutdown()

if __name__=='__main__':main()
