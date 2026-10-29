"""Verified text replacement and file-upload preflight for the pinned browser CLI."""
import json
import re
from pathlib import Path
import subprocess
import time
import uuid


class FormError(ValueError):
    pass


def native(binary, env, args, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FormError('Form operation timed out. Inspect the current page before retrying.')
    result = subprocess.run([binary, *args], env=env, capture_output=True, text=True,
                            timeout=max(.1, remaining))
    if result.returncode:
        # Do not echo arbitrary page output or entered text into diagnostics.
        raise FormError(f'Browser command {args[0]} failed (exit {result.returncode}); inspect the page.')
    return result


def command_data(binary, env, args, deadline):
    result = native(binary, env, [*args, '--json'], deadline)
    try:
        value = json.loads(result.stdout)
        if not value.get('success'):
            raise FormError('Browser evaluation failed; inspect the page.')
        return value['data']
    except (ValueError, KeyError, TypeError) as exc:
        raise FormError('Unexpected browser result; operation not verified.') from exc


def evaluate(binary, env, script, deadline):
    data = command_data(binary, env, ['eval', script], deadline)
    if 'result' not in data:
        raise FormError('Browser evaluation result missing; operation not verified.')
    return data['result']


def replace_text(binary, env, selector, text, timeout=75):
    """Select the exact focused editing host, edit via CDP, and verify readback.

    No value/innerHTML assignments: controlled inputs and rich editors must receive
    native editing events. Hold the caller's lease throughout this sequence.
    """
    if len(text.encode('utf-8')) > 100000:
        raise FormError('Text exceeds the 100 KB form limit.')
    deadline = time.monotonic() + timeout
    key = '__qq_edit_' + uuid.uuid4().hex
    slot = json.dumps(key)
    marker = 'data-qq-edit-' + uuid.uuid4().hex
    # Native get-count accepts CSS but not snapshot refs; a ref identifies one node.
    if not re.fullmatch(r'@e[0-9]+', selector):
        count = command_data(binary, env, ['get', 'count', selector], deadline)
        if count.get('count') != 1:
            raise FormError('Text selector must identify exactly one element; refresh the snapshot.')
    native(binary, env, ['focus', selector], deadline)
    try:
        info = evaluate(binary, env, """(()=>{
let e=document.activeElement;
while(e?.shadowRoot?.activeElement)e=e.shadowRoot.activeElement;
if(!e||e.disabled||e.readOnly)throw Error('Editable target required');
let kind=e.tagName.toLowerCase();
if(kind==='input'&&!['text','search','url','tel','email','password'].includes(e.type))throw Error('Not a text input');
if(!['input','textarea'].includes(kind)&&!e.isContentEditable)throw Error('Not an editing host');
if(e.isContentEditable){while(e.parentElement?.isContentEditable)e=e.parentElement;}
const r=e.getBoundingClientRect();
if(!r.width||!r.height)throw Error('Hidden editing host');
window[SLOT]=e;
e.setAttribute(MARKER,'1');
e.focus();
if(kind==='input'||kind==='textarea')e.select();
else{const r=document.createRange();r.selectNodeContents(e);const s=window.getSelection();s.removeAllRanges();s.addRange(r);}
return {kind:kind,editable:true};
})()""".replace('SLOT', slot).replace('MARKER', json.dumps(marker)), deadline)
        if not isinstance(info, dict) or info.get('editable') is not True:
            raise FormError('Target is not a verified text editor.')
        matched = command_data(binary, env, ['get', 'attr', selector, marker], deadline)
        if matched.get('value') != '1':
            raise FormError('Requested editor did not receive focus; no text was changed.')
        native(binary, env, ['press', 'Backspace'], deadline)
        read = """(()=>{
const e=window[SLOT];
if(!e?.isConnected)throw Error('Editor replaced; locate it again');
let a=document.activeElement;while(a?.shadowRoot?.activeElement)a=a.shadowRoot.activeElement;
if(a!==e&&!e.contains(a))throw Error('Editor lost focus');
function logical(node){
 if(node.nodeType===3)return node.nodeValue;
 if(node.nodeType!==1)return '';
 if(node.tagName==='BR')return '\\n';
 const children=Array.from(node.childNodes);
 if(children.length===1&&children[0].nodeName==='BR')return '';
 let result='',previousBlock=false;
 children.forEach((child,i)=>{
  const block=child.nodeType===1&&/^(P|DIV|H[1-6]|LI|BLOCKQUOTE|PRE)$/.test(child.tagName);
  if(i>0&&(block||previousBlock))result+='\\n';
  result+=logical(child);previousBlock=block;
 });
 return result;
}
const raw=('value' in e?e.value:e.innerText).replace(/\\r\\n?/g,'\\n');
const text=('value' in e?e.value:logical(e)).replace(/\\r\\n?/g,'\\n');
return {matches:text===EXPECTED,characters:text.length,paragraph_normalized:raw!==text};
})()""".replace('SLOT', slot)
        cleared = evaluate(binary, env, read.replace('EXPECTED', '""'), deadline)
        if not cleared.get('matches'):
            raise FormError('Editor did not clear; replacement stopped before inserting text.')
        if text:
            native(binary, env, ['keyboard', 'inserttext', text], deadline)
        expected = json.dumps(text.replace('\r\n', '\n').replace('\r', '\n'), ensure_ascii=True)
        verified = evaluate(binary, env, read.replace('EXPECTED', expected), deadline)
        if not verified.get('matches'):
            raise FormError('Editor readback differs from intended text; inspect before further edits or submission.')
        # A second read on another CLI round trip catches immediate controlled-input rerenders.
        verified = evaluate(binary, env, read.replace('EXPECTED', expected), deadline)
        if not verified.get('matches'):
            raise FormError('Editor reverted after input; replacement is not verified.')
        return {'success': True, 'verified': True, 'characters': verified['characters'],
                'paragraph_normalized': verified.get('paragraph_normalized', False),
                'notice': 'Editor text matches. Verify application preview/validation before submitting.'}
    finally:
        # Cleanup is best effort and never changes the edited element.
        try:
            evaluate(binary, env, '(()=>{window[' + slot + ']?.removeAttribute(' + json.dumps(marker) + ');delete window[' + slot + '];return true})()', time.monotonic() + 3)
        except (FormError, subprocess.TimeoutExpired):
            pass


def execute(binary, env, args, timeout=75):
    if args[0] in ('fill', 'replace-text'):
        if args[0] == 'replace-text' and len(args) == 4 and args[2] == '--file':
            path = Path(args[3]).expanduser()
            if not path.is_file() or path.stat().st_size > 100000:
                raise FormError('Text file must exist and be at most 100 KB.')
            text = path.read_text(encoding='utf-8')
        elif len(args) == 3:
            text = args[2]
        else:
            raise FormError('Use fill SELECTOR TEXT or replace-text SELECTOR --file UTF8_FILE.')
        result = replace_text(binary, env, args[1], text, timeout)
        print(json.dumps(result, ensure_ascii=False))
        return subprocess.CompletedProcess(args, 0)
    if args[0] == 'upload':
        if len(args) < 3:
            raise FormError('Use upload SELECTOR FILE [FILE ...].')
        paths = []
        for filename in args[2:]:
            path = Path(filename).expanduser().resolve()
            if not path.is_file():
                raise FormError('Upload file does not exist or is not a regular file.')
            with path.open('rb') as stream:
                stream.read(1)
            paths.append(str(path))
        result = subprocess.run([binary, 'upload', args[1], *paths], env=env, timeout=timeout)
        if result.returncode == 0:
            print(json.dumps({'files_selected': len(paths), 'application_upload_verified': False,
                'notice': 'Wait for editor thumbnails/upload completion; verify attachment count and order before submitting.'}))
        return result
    return subprocess.run([binary, *args], env=env, timeout=timeout)
