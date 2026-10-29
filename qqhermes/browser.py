"""Conversation-scoped agent-browser launcher with one resource lease per container.

This is routing/resource isolation for our trusted single-UID deployment, not an
OS security boundary. Chromium and all browser sessions remain in the bot container.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BINARY = '/opt/agent-browser-cli/node_modules/.bin/agent-browser'
LEASE = Path('/tmp/qq-agent-browser-lease')
IDLE_MS = 120000
COMMAND_TIMEOUT = 75
MANAGED_FLAGS = {'--session','--namespace','--session-name','--profile','--restore','--restore-save',
    '--state','--cdp','--auto-connect','--provider','-p','--headed','--executable-path','--engine',
    '--config','--args','--idle-timeout','--download-path','--screenshot-dir'}


def scope(environ):
    from .conversation import conversation
    from .settings import settings
    home=Path(environ.get('HERMES_HOME','')).resolve()
    if not home.name.startswith('qq-') or home.parent.name!='profiles':
        raise PermissionError('Use agent-browser from a trusted QQ conversation profile.')
    chat=conversation(home.name[3:])
    if not chat.permitted(settings()):raise PermissionError('Untrusted browser conversation.')
    session=environ.get('HERMES_SESSION_ID') or environ.get('HERMES_SESSION_KEY') or 'foreground'
    message=environ.get('HERMES_SESSION_MESSAGE_ID','')
    identity=hashlib.sha256(f'{session}:{message}'.encode()).hexdigest()[:16]
    return chat,home,identity


def prepare(argv,environ):
    chat,home,identity=scope(environ)
    args=list(argv);job='';persistent=False
    if args[:1]==['--job']:
        if len(args)<3 or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',args[1]):
            raise ValueError('--job requires a short alphanumeric job name and command.')
        job=args[1];args=args[2:]
    if args[:1]==['--persistent']:
        persistent=True;args=args[1:]
    for arg in args:
        if arg.split('=',1)[0] in MANAGED_FLAGS:
            raise ValueError('Browser session/launch flags are managed. Use --job NAME or --persistent instead.')
    if not args:raise ValueError('Load instructions with agent-browser skills get core, or use --help.')
    if args[0] in ('install','upgrade','dashboard','connect','auth','mcp') or (args[0]=='close' and '--all' in args):
        raise ValueError('Installation, dashboard, cross-session access and credential vault are not available here.')
    session=identity+('-'+hashlib.sha256(job.encode()).hexdigest()[:10] if job else '')
    browser_home=home/'cache'/'agent-browser-home'
    workspace=chat.directory/'workspace';downloads=workspace/'browser-downloads'
    screenshots=workspace/'browser-screenshots'
    for directory in (browser_home,downloads,screenshots):directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    # Do not pass provider keys or inherited cross-session browser settings into the daemon.
    env={k:v for k,v in environ.items() if k in ('PATH','LANG','LC_ALL','TZ','SSL_CERT_FILE','SSL_CERT_DIR','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy')}
    env.update(HOME=str(browser_home),XDG_CACHE_HOME=str(browser_home/'.cache'),XDG_CONFIG_HOME=str(browser_home/'.config'),
        AGENT_BROWSER_NAMESPACE=chat.profile,AGENT_BROWSER_SESSION=session,
        AGENT_BROWSER_SOCKET_DIR='/tmp/ab/'+chat.profile,
        AGENT_BROWSER_EXECUTABLE_PATH='/usr/bin/chromium',AGENT_BROWSER_IDLE_TIMEOUT_MS=str(IDLE_MS),
        AGENT_BROWSER_DEFAULT_TIMEOUT='25000',AGENT_BROWSER_NO_WEBMCP='1',AGENT_BROWSER_CONTENT_BOUNDARIES='1',
        AGENT_BROWSER_MAX_OUTPUT='16000',AGENT_BROWSER_DOWNLOAD_PATH=str(downloads),AGENT_BROWSER_SCREENSHOT_DIR=str(screenshots))
    if persistent:
        # Persistent data is local to this conversation and job. Opt in only when requested.
        profile=home/'browser-profiles'/(job or 'personal');profile.mkdir(parents=True,mode=0o700,exist_ok=True)
        env['AGENT_BROWSER_PROFILE']=str(profile)
    return args,env,{'chat':chat.chat_id,'session':session,'home':str(browser_home),'persistent':persistent}


def run(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    # Reading packaged documentation doesn't need a profile or browser lease.
    if args[:1] in (['--help'],['-h'],['--version']) or args[:2] in (['skills','list'],['skills','get']):
        return subprocess.run([BINARY,*args],timeout=30).returncode
    args,env,owner=prepare(args,os.environ)
    LEASE.parent.mkdir(parents=True,exist_ok=True)
    with LEASE.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        lock.seek(0)
        try:previous=json.load(lock)
        except (ValueError,TypeError):previous={}
        same=all(previous.get(k)==owner.get(k) for k in ('chat','session','persistent'))
        if previous and not same:
            age=time.time()-previous.get('updated',0)
            if age<(IDLE_MS/1000+15):
                print(json.dumps({'error':'browser_busy','retry_after_seconds':max(1,int(IDLE_MS/1000+15-age)),
                    'notice':'Another browser task holds the slot. Do not poll repeatedly; continue lightweight work or retry later.'}))
                return 75
            # Native idle shutdown should already have run; close once to verify cleanup.
            old_env={**env,'HOME':previous['home'],'AGENT_BROWSER_NAMESPACE':'qq-'+previous['chat'],
                     'AGENT_BROWSER_SESSION':previous['session'],
                     'AGENT_BROWSER_SOCKET_DIR':'/tmp/ab/qq-'+previous['chat']}
            old_env.pop('AGENT_BROWSER_PROFILE',None)
            old_env['XDG_CACHE_HOME']=str(Path(previous['home'])/'.cache')
            old_env['XDG_CONFIG_HOME']=str(Path(previous['home'])/'.config')
            closed=subprocess.run([BINARY,'close'],env=old_env,capture_output=True,text=True,timeout=30)
            if closed.returncode:
                print(json.dumps({'error':'browser_cleanup_failed','notice':'Previous browser could not be closed; slot remains reserved.'}))
                return 75
        owner['updated']=time.time()
        lock.seek(0);lock.truncate();json.dump(owner,lock);lock.flush()
        try:
            result=subprocess.run([BINARY,*args],env=env,timeout=COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(json.dumps({'error':'browser_command_timeout','notice':'Inspect current page state before repeating a click or form submission.'}))
            return 124
        finally:
            owner['updated']=time.time()
            lock.seek(0);lock.truncate();json.dump(owner,lock);lock.flush()
        if args[0] in ('close','quit','exit') and result.returncode==0:
            lock.seek(0);lock.truncate();lock.flush()
        return result.returncode


def main():
    try:raise SystemExit(run())
    except (ValueError,PermissionError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({'error':type(exc).__name__,'notice':str(exc)}));raise SystemExit(2)

if __name__=='__main__':main()
