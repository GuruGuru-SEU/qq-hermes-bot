"""Real Chromium form regression fixture; no external websites/accounts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from PIL import Image

HTML = '''<!doctype html><meta charset="utf-8"><title>Browser forms fixture</title>
<a href="#navigation">发布笔记（导航）</a>
<input id="files" type="file" accept="image/png,image/jpeg" multiple hidden>
<output id="uploads" aria-label="Uploaded files"></output>
<label>Title<input id="title" aria-label="Title" value="old title"></label>
<label>Controlled<input id="controlled" aria-label="Controlled" value="old controlled"></label>
<label>Read only<input id="readonly" readonly value="do not change"></label>
<textarea id="area" aria-label="Plain body">old plain body</textarea>
<div id="editor" role="textbox" aria-label="Rich body" contenteditable="true"><p>old rich</p><p>old paragraph</p></div>
<input id="disabled" disabled value="do not change"><div class="duplicate" contenteditable>one</div><div class="duplicate" contenteditable>two</div>\n<input id="number" type="number" value="7"><button id="uneditable">Not an editor</button>
<output id="receipt">Nothing submitted</output>
<fixture-actions></fixture-actions>
<script>
const t=document.getElementById('controlled');
const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
let last=t.value;window.controlledState=t.value;
Object.defineProperty(t,'value',{get(){return d.get.call(this)},set(v){last=String(v);d.set.call(this,v)}});
t.addEventListener('input',()=>{if(t.value!==last){last=t.value;window.controlledState=t.value}});
window.events=0;document.getElementById('editor').addEventListener('input',()=>window.events++);
document.getElementById('files').addEventListener('change',e=>{
 window.fileNames=Array.from(e.target.files).map(f=>f.name);
 document.getElementById('uploads').textContent='Uploading';
 setTimeout(()=>document.getElementById('uploads').textContent='Ready: '+window.fileNames.join(','),150);
});
window.saved=0;window.submitted=0;
customElements.define('fixture-actions',class extends HTMLElement{
 connectedCallback(){
 const shadow=this.attachShadow({mode:'closed'});
 shadow.innerHTML='<div style="display:flex;gap:30px;padding:20px"><button>暂存离开</button><button>发布</button></div>';
 shadow.querySelectorAll('button')[0].onclick=()=>{window.saved++;document.getElementById('receipt').textContent='Draft saved';};
 shadow.querySelectorAll('button')[1].onclick=()=>{window.submitted++;document.getElementById('receipt').textContent='Submitted';};
 }});
</script>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.end_headers()
        self.wfile.write(HTML.encode())
    def log_message(self,*args):pass


def main():
    with tempfile.TemporaryDirectory(prefix='qq-forms-') as tmp:
        root=Path(tmp);home=root/'profiles/qq-900001';home.mkdir(parents=True)
        config=root/'config.json';config.write_text(json.dumps({'trusted_groups':[900001],'trusted_users':[]}))
        env=dict(os.environ,HERMES_HOME=str(home),QQ_STATE=str(root/'state'),QQ_CONFIG=str(config),
                 HERMES_SESSION_ID='forms',HERMES_SESSION_MESSAGE_ID='1')
        env.pop('PYTHONPATH',None)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        def call(*args,check=True):
            result=subprocess.run(['agent-browser',*args],env=env,text=True,capture_output=True,timeout=90)
            if check: assert result.returncode==0,(args,result.stdout,result.stderr)
            print(json.dumps({'command':args[0],'exit':result.returncode,'output':result.stdout[:180]},ensure_ascii=False),flush=True)
            return result
        def evaluate(js):
            return json.loads(call('eval',js,'--json').stdout)['data']['result']
        try:
            call('--temporary','open',f'http://127.0.0.1:{server.server_port}/')
            snap=call('snapshot','-i').stdout
            assert 'Title' in snap and 'Rich body' in snap
            import re
            ref=re.search(r'textbox "Title".*?ref=(e\d+)',snap).group(1)
            call('fill','@'+ref,'中文标题 👀')
            assert evaluate('document.querySelector("#title").value')=='中文标题 👀'
            call('fill','#controlled','')
            assert evaluate('window.controlledState')==''
            call('fill','#controlled','新的受控内容')
            assert evaluate('window.controlledState')=='新的受控内容'
            call('fill','#editor','')
            assert not evaluate('document.querySelector("#editor").innerText').strip()
            text='中文第一段 👀\n\n第二段含 "引号" $HOME $(id)\n最后一行'
            file=root/'body.txt';file.write_text(text)
            replacement=call('replace-text','#editor','--file',str(file),check=False)
            if replacement.returncode:
                print('EDITOR_DIAGNOSTIC',evaluate('({text:document.querySelector("#editor").innerText,html:document.querySelector("#editor").innerHTML})'),flush=True)
            assert replacement.returncode==0,replacement.stdout
            assert evaluate('document.querySelector("#editor").textContent')==text.replace('\n','')
            assert evaluate('Array.from(document.querySelector("#editor").children).map(e=>e.textContent)')==['','第二段含 "引号" $HOME $(id)','最后一行']
            call('fill','#editor','短内容')
            assert evaluate('document.querySelector("#editor").innerText')=='短内容'
            call('replace-text','#area','--file',str(file))
            assert evaluate('document.querySelector("#area").value')==text
            before=evaluate('document.querySelector("#editor").innerText')
            assert call('fill','#disabled','bad',check=False).returncode!=0
            assert call('fill','.duplicate','bad',check=False).returncode!=0
            assert call('fill','#readonly','bad',check=False).returncode!=0
            assert call('fill','#uneditable','bad',check=False).returncode!=0
            assert call('fill','#missing','bad',check=False).returncode!=0
            assert call('fill','#number','bad',check=False).returncode!=0
            assert evaluate('document.querySelector("#editor").innerText')==before
            images=[]
            for name,color in [('first.png','red'),('second.png','blue')]:
                p=root/name;Image.new('RGB',(20,20),color).save(p);images.append(str(p))
            call('upload','#files',*images)
            call('wait','--fn','document.querySelector("#uploads").textContent.startsWith("Ready:")')
            assert evaluate('window.fileNames')==['first.png','second.png']
            assert call('upload','#files',images[0],str(root/'missing.png'),check=False).returncode!=0
            assert evaluate('window.fileNames')==['first.png','second.png']
            # Accessibility refs reach distinct controls inside closed shadow DOM.
            snap=call('snapshot','-i').stdout
            publish=re.search(r'button "发布".*?ref=(e\d+)',snap)
            assert publish,snap
            call('click','@'+publish.group(1))
            assert evaluate('({saved:window.saved,submitted:window.submitted,receipt:document.querySelector("#receipt").textContent})')=={'saved':0,'submitted':1,'receipt':'Submitted'}
            print('FORMS PASSED: exact clear/replace, controlled state, refs, Unicode/paragraphs, invalid targets, hidden multi-file upload/order, separate shadow save/submit',flush=True)
        finally:
            call('close',check=False);server.shutdown()

if __name__=='__main__':main()
