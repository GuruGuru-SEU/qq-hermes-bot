"""Real Chromium handoff/checkpoint tests; synthetic website, no real orders."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading


class Handler(BaseHTTPRequestHandler):
    handoffs=0
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
        if self.path.startswith('/handoff'):
            type(self).handoffs+=1
            if self.handoffs==1:
                body='<title>Handoff fixture</title><body>NEW<script>console.error("fixture initialization failed");setTimeout(()=>{throw new Error("fixture failure")},10)</script>'
            else:
                body='<title>Recovered handoff</title><body>Original source-linked file ready<script>window.receipt={id:new URL(location.href).searchParams.get("fileId"),source:new URL(location.href).searchParams.get("source"),fragment:location.hash}</script>'
        else:body='<title>Other page</title><body>Other page'
        self.wfile.write(body.encode())
    def log_message(self,*args):pass


def main():
    with tempfile.TemporaryDirectory(prefix='qq-recovery-') as tmp:
        root=Path(tmp);home=root/'profiles/qq-900001';home.mkdir(parents=True)
        config=root/'config.json';config.write_text(json.dumps({'trusted_groups':[900001],'trusted_users':[]}))
        env=dict(os.environ,HERMES_HOME=str(home),QQ_CONFIG=str(config),QQ_STATE=str(root/'state'),
                 HERMES_SESSION_ID='recovery-fixture',HERMES_SESSION_MESSAGE_ID='1')
        env.pop('PYTHONPATH',None)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        def call(*args):
            r=subprocess.run(['agent-browser',*args],env=env,capture_output=True,text=True,timeout=90)
            assert r.returncode==0,(args,r.stdout,r.stderr)
            return r.stdout
        def ev(js):return json.loads(call('eval',js,'--json'))['data']['result']
        url=f'http://127.0.0.1:{server.server_port}/handoff?fileId=123456789012345&source=oneclick&token=fixture-secret#/eda?campaign=opaque'
        try:
            call('--account','test','open',url)
            call('wait','--fn','document.body.innerText.trim()==="NEW"')
            saved=json.loads(call('checkpoint','original'))
            assert 'fixture-secret' not in json.dumps(saved)
            report=json.loads(call('diagnose'))
            assert report['page']['visibleCharacters']==3,report
            assert any('fixture' in x['text'] for x in report['console']),report
            assert not report['collection_errors'],report
            assert Handler.handoffs==1,'diagnose must not navigate or replay'
            call('network','console')  # Alias accepted without hiding an error.
            call('open',f'http://127.0.0.1:{server.server_port}/other')
            call('close')
            restored=json.loads(call('resume','original'))
            assert 'fixture-secret' not in json.dumps(restored)
            assert ev('location.href')==url
            assert ev('window.receipt')=={'id':'123456789012345','source':'oneclick','fragment':'#/eda?campaign=opaque'}
            assert Handler.handoffs==2,'resume navigates exactly once'
            print('BROWSER RECOVERY PASSED: blank-page diagnostics, exact URL, source association, persisted checkpoint, no retries',flush=True)
        finally:
            call('close');server.shutdown()


if __name__=='__main__':main()
