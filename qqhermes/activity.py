"""Bounded, redacted foreground tool details and ordinary assistant progress text."""
from collections import deque
import copy
import re
import shlex
import sys
import threading
import time
from types import ModuleType
from urllib.parse import urlsplit

from .settings import root

# A standalone whitespace-delimited command, optionally followed by punctuation.
# URLs, fragments, /status and #statusfoo remain normal conversation text.
_TRIGGER=re.compile(r'(?<!\S)#status(?=$|[\s，。！？!?；;：:])',re.I)
# Hermes loads one namespaced plugin module per profile. Share only this bounded
# receipt store across those modules so the primary adapter can inspect its lane.
_state=ModuleType('_qq_hermes_activity_state')
_state.lock=threading.Lock()
_state.turns={}
_state=sys.modules.setdefault(_state.__name__,_state)
_LOCK=_state.lock
_TURNS=_state.turns


def is_status(text):
    return bool(_TRIGGER.search(text))


def is_stop(text):
    # Destructive control requires the whole text, not quoted/embedded prose.
    return bool(re.fullmatch(r'\s*#stop[。！!]?\s*',text,re.I))


def stop_summary(turn):
    if not turn:return '准备处理请求'
    running=list(dict.fromkeys(s['label'] for s in turn['running'].values()))
    stage='、'.join(running[:3]) if running else turn['phase']
    return f'已运行 {int(time.monotonic()-turn["started"])} 秒，正在{stage}'


def _key(chat):
    return str(root().resolve()),str(chat)


def begin(chat,mid,user):
    now=time.monotonic()
    with _LOCK:
        _TURNS[_key(chat)]={'mid':str(mid),'user':str(user),'started':now,'updated':now,
            'session':'','phase':'准备上下文与附件','requests':0,'steps':deque(maxlen=8),'running':{}}


def finish(chat,mid):
    with _LOCK:
        key=_key(chat)
        if _TURNS.get(key,{}).get('mid')==str(mid):_TURNS.pop(key,None)


def snapshot(chat):
    with _LOCK:return copy.deepcopy(_TURNS.get(_key(chat)))


def phase(chat,mid,text):
    with _LOCK:
        turn=_TURNS.get(_key(chat))
        if turn and turn['mid']==str(mid) and turn['phase']!=text:
            turn['phase']=text;turn['updated']=time.monotonic()


def _scope():
    from gateway.session_context import get_session_env
    from agent.delegation_context import is_delegated_child_context
    from .conversation import conversation_for_profile
    if is_delegated_child_context() or get_session_env('HERMES_CRON_SESSION','')=='1':return None
    try:chat=conversation_for_profile()
    except PermissionError:return None
    if get_session_env('HERMES_SESSION_PLATFORM','')!='qq_onebot':return None
    if str(get_session_env('HERMES_SESSION_CHAT_ID',''))!=chat.chat_id:return None
    mid=str(get_session_env('HERMES_SESSION_MESSAGE_ID',''))
    return _key(chat.key),mid


def _current(scope,session_id,allow_rotation=False):
    if not scope:return None
    key,mid=scope
    turn=_TURNS.get(key)
    if not turn or turn['mid']!=mid:return None
    # A background/internal session must never overwrite foreground receipts.
    if turn['session'] and session_id and turn['session']!=session_id:
        from gateway.session_context import get_session_env
        if not allow_rotation or get_session_env('HERMES_SESSION_ID','')!=session_id:return None
    if session_id:turn['session']=session_id
    return turn


def _host(value):
    try:
        host=urlsplit(str(value)).hostname or ''
        return host if re.fullmatch(r'[a-zA-Z0-9.-]{1,100}',host) else ''
    except ValueError:return ''


def tool_label(name,args):
    """Allowlisted operation metadata only: never dump arbitrary args or outputs."""
    args=args if isinstance(args,dict) else {}
    labels={'web_search':'网页搜索','web_extract':'网页抓取','web_fetch':'网页抓取',
        'tool_search':'查找可用工具','tool_describe':'读取工具说明',
        'browser_navigate':'浏览器打开网页','browser_click':'浏览器点击',
        'browser_snapshot':'浏览器检查页面','vision_analyze':'分析图片',
        'read_file':'读取文件','write_file':'写入文件','patch':'修改文件',
        'search_files':'搜索文件','qq_history':'查询会话历史','qq_image':'读取聊天图片',
        'qq_file':'下载聊天文件','qq_send':'发送消息或附件','skill_view':'读取技能',
        'skills_list':'查找技能','skill_manage':'管理技能','memory':'更新记忆',
        'cronjob_manage':'管理定时任务','cronjob':'管理定时任务','delegate_task':'管理后台任务',
        'process':'查看进程','tool_call':'调用工具','terminal':'执行终端命令'}
    label=labels.get(name,'执行工具')
    if name in ('web_extract','web_fetch'):
        host=_host(args.get('url',''))
        if host:label+=' · '+host
    if name=='terminal':
        command=args.get('command','')
        if isinstance(command,str) and len(command)<32768:
            try:words=shlex.split(command)
            except ValueError:words=[]
            for i,word in enumerate(words):
                if word.rsplit('/',1)[-1]=='agent-browser':
                    tail=words[i+1:]
                    while len(tail)>=2 and tail[0] in ('--account','--job'):tail=tail[2:]
                    while tail and tail[0] in ('--persistent','--temporary'):tail=tail[1:]
                    action=tail[0] if tail else ''
                    title={'open':'打开网页','click':'点击','fill':'填写文本','replace-text':'替换文本',
                        'snapshot':'检查页面','screenshot':'截图','upload':'上传文件','eval':'检查页面脚本',
                        'wait':'等待页面','close':'关闭','keyboard':'键盘操作','press':'按键',
                        'get':'读取页面','scroll':'滚动','login-wait':'等待扫码',
                        'browser-status':'查询状态'}.get(action,'操作')
                    return '浏览器'+title
            if any(w.rsplit('/',1)[-1] in ('python','python3','uv') for w in words[:3]):
                return '运行 Python / uv'
    return label


def preview(value,limit=420):
    """Redact BEFORE truncation/storage; never allow a preview to break the turn."""
    if not isinstance(value,str):return ''
    if len(value)>32768:return '[内容过长，未展示]'
    try:
        from agent.redact import redact_sensitive_text
        value=redact_sensitive_text(value,force=True,redact_url_credentials=True)
        # CLI flags are not necessarily assignments or credential-shaped tokens.
        value=re.sub(r'''(?ix)(--(?:password|passwd|token|api[-_]key|secret|client[-_]secret|access[-_]token|cookie|authorization)(?:=|\s+))(?:"[^"]*"|'[^']*'|[^\s;]+)''',
                     r'\1[REDACTED]',value)
        value=re.sub(r'[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]','',value)
        value=value.replace('\r\n','\n').replace('\n',' ⏎ ').replace('\t','  ').strip()
        return value if len(value)<=limit else value[:limit]+' …[已截断]'
    except Exception:return '[详情脱敏失败，未展示]'


def tool_detail(name,args):
    args=args if isinstance(args,dict) else {}
    if name=='terminal':
        command=preview(args.get('command'))
        cwd=preview(args.get('cwd'),100)
        return command+(f'（cwd: {cwd}）' if cwd else '')
    if name in ('read_file','write_file','patch','search_files'):
        path=preview(args.get('path') or args.get('file_path'),300)
        bounds=[]
        for key,label in (('offset','起始行'),('limit','行数')):
            if type(args.get(key)) is int:bounds.append(f'{label} {args[key]}')
        return path+('（'+', '.join(bounds)+'）' if bounds else '')
    if name in ('web_extract','web_fetch'):return preview(args.get('url'))
    if name=='web_search':return preview(args.get('query'))
    if name in ('skill_view','tool_describe'):return preview(args.get('name') or args.get('skill_name'))
    if name in ('qq_file','qq_image'):
        fields=[f'{k}={args[k]}' for k in ('action','message_id','position') if isinstance(args.get(k),(str,int))]
        return preview(' '.join(fields))
    if name=='process':return preview(' '.join(str(args[k]) for k in ('action','session_id') if k in args))
    return ''


def _field(obj,name,default=None):
    return obj.get(name,default) if isinstance(obj,dict) else getattr(obj,name,default)


def model_execution(request,next_call,session_id='',turn_id='',**kwargs):
    """Observe a completed provider response before its tools start. Never replay it."""
    scope=_scope()
    response=next_call(request)
    try:
        choices=_field(response,'choices',[]) or []
        message=_field(choices[0],'message') if choices else None
        # Only ordinary assistant text accompanying tools: no final-answer preview,
        # historical messages, reasoning_content, thinking blocks or tool output.
        if not turn_id or not _field(message,'tool_calls'):return response
        content=_field(message,'content','')
        if isinstance(content,list):
            content='\n'.join(_field(part,'text','') for part in content
                              if _field(part,'type') in ('text','output_text') and isinstance(_field(part,'text'),str))
        text=preview(content,320)
        if not text:return response
        with _LOCK:
            turn=_current(scope,session_id)
            if turn is not None:
                now=time.monotonic()
                turn['steps'].append({'kind':'message','text':text,'started':now})
                turn['updated']=now
    except Exception:pass  # Observability must not turn a successful request into a retry.
    return response


def model_request(request,session_id='',turn_id='',**kwargs):
    if not turn_id:return
    scope=_scope()
    with _LOCK:
        turn=_current(scope,session_id,allow_rotation=True)
        if turn is None:return
        turn['requests']+=1
        turn['phase']='模型处理中（思考或生成回复）'
        turn['updated']=time.monotonic()
    # Observe only; no request content is read, modified or retained.


def auxiliary_started(aux_task='',session_id='',api_request_id='',approx_input_tokens=None,**kwargs):
    if aux_task!='compression':return
    scope=_scope()
    with _LOCK:
        turn=_current(scope,session_id)
        if turn is None:return
        now=time.monotonic()
        step={'id':'compression:'+api_request_id,'label':'生成上下文摘要',
              'detail':f'摘要输入约 {approx_input_tokens:,} tokens' if type(approx_input_tokens) is int else '',
              'started':now,'ended':None,'state':'运行中'}
        turn['steps'].append(step);turn['running'][step['id']]=step
        turn['phase']='正在压缩会话上下文';turn['updated']=now


def auxiliary_finished(aux_task='',session_id='',api_request_id='',error=None,**kwargs):
    if aux_task!='compression':return
    scope=_scope()
    with _LOCK:
        turn=_current(scope,session_id)
        if turn is None:return
        step=turn['running'].pop('compression:'+api_request_id,None)
        if step is None:return
        step['ended']=time.monotonic();step['state']='失败' if error else '完成'
        turn['phase']='上下文摘要失败' if error else '摘要已返回，正在整理会话'
        turn['updated']=step['ended']


def tool_started(tool_name,args,session_id='',tool_call_id='',**kwargs):
    if tool_name=='tool_call':return  # Actual nested tools emit their own receipts.
    scope=_scope()
    with _LOCK:
        turn=_current(scope,session_id)
        if turn is None:return
        now=time.monotonic()
        step={'id':tool_call_id or tool_name,'label':tool_label(tool_name,args),'detail':tool_detail(tool_name,args),
              'started':now,'ended':None,'state':'运行中'}
        turn['steps'].append(step)
        if len(turn['running'])<32:turn['running'][step['id']]=step
        turn['updated']=now;turn['phase']='执行工具'


def tool_finished(tool_name,args=None,result=None,session_id='',tool_call_id='',status=None,**kwargs):
    if tool_name=='tool_call':return
    scope=_scope()
    with _LOCK:
        turn=_current(scope,session_id)
        if turn is None:return
        step=turn['running'].pop(tool_call_id or tool_name,None)
        if step is None:return
        step['ended']=time.monotonic()
        step['state']={'success':'完成','ok':'完成','error':'失败','failed':'失败',
                       'cancelled':'已取消','blocked':'被阻止','timeout':'超时'}.get(status,'已返回')
        turn['updated']=step['ended']
        turn['phase']='执行工具' if turn['running'] else '处理工具结果'


def render(chat,queued):
    turn=snapshot(chat)
    label='本私聊' if str(chat).startswith('dm-') else '本群'
    if turn is None:return f'{label}当前没有正在处理的前台请求。\n排队：{queued} 条。'
    now=time.monotonic()
    session=turn['session']
    session=session[-16:] if re.fullmatch(r'[\w-]{1,200}',session or '') else '尚未取得会话 ID'
    lines=[f'{label}前台会话：{session}',f'已运行 {int(now-turn["started"])} 秒；排队 {queued} 条',
           f'当前：{turn["phase"]}；模型请求 {turn["requests"]} 次']
    if turn['running']:
        lines.append('正在执行：'+'、'.join(dict.fromkeys(s['label'] for s in turn['running'].values())))
    if turn['steps']:
        lines.append('最近活动（旧→新）：')
        for step in turn['steps']:
            at=int(step['started']-turn['started'])
            if step.get('kind')=='message':
                lines.append(f'· +{at}s 模型进展：{step["text"]}')
                continue
            elapsed=int((step['ended'] or now)-step['started'])
            lines.append(f'· +{at}s {step["label"]} — {step["state"]}，{elapsed} 秒')
            if step.get('detail'):lines.append('  '+step['detail'])
    else:lines.append('尚无工具调用记录。')
    age=int(now-turn['updated'])
    lines.append(f'最近活动：{age} 秒前。')
    return '\n'.join(lines)
