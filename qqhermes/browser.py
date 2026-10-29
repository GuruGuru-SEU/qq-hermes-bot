"""Conversation-scoped Chromium profiles and bounded cross-turn login handoffs.

The launcher coordinates trusted users sharing one container UID; it is not an
OS security boundary. Disk profiles outlive daemons, messages and jobs.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from .browser_identity import user_agent

BINARY = '/opt/agent-browser-cli/node_modules/.bin/agent-browser'
LEASE = Path('/tmp/qq-agent-browser-lease')
IDLE_MS = 120000
PERSISTENT_IDLE_MS = 600000
COMMAND_TIMEOUT = 75
MANAGED_FLAGS = {
    '--session', '--namespace', '--session-name', '--profile', '--restore',
    '--restore-save', '--state', '--cdp', '--auto-connect', '--provider', '-p',
    '--headed', '--executable-path', '--engine', '--config', '--args',
    '--idle-timeout', '--download-path', '--screenshot-dir', '--user-agent',
}
HELP = """QQ browser routing (before the native command):
  --account NAME        Use a named, persistent account in this conversation
  --persistent          Use the selected account, or legacy account 'personal'
  --temporary           Use a disposable browser for this turn
  --job NAME            Isolate a background job; combine with --account if needed
  browser-status        Inspect accounts, selection and login wait without launching
  diagnose              Read bounded page/errors/console/network diagnostics
  checkpoint NAME       Save this tab's exact URL privately for this account
  resume NAME           Open that exact URL once; verify resulting page state
  login-wait            Keep the current persistent tab for up to 10 minutes
  login-done            End the wait; verify the authenticated page before claiming login
  login-cancel          Close the pending tab, retaining saved profile data
  remember URL [...]    Associate website origins with the selected account
  forget-account NAME --confirm   Delete this account's local browser data
  replace-text SELECTOR --file FILE   Replace from UTF-8 file and verify readback
  fill SELECTOR TEXT    Verified replacement for inputs, textarea and rich text
  upload SELECTOR FILE [...]   Validate files; then verify thumbnails on the page
close releases Chromium and preserves saved accounts. It does not log out.
Public URLs use temporary mode unless this turn selected an account or their
origin is remembered. Commands without a URL resume this lane's selected browser.
"""


def named(value):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', value):
        raise ValueError('Account/job names must be 1-40 ASCII letters, digits, underscores or hyphens.')
    return value


def website(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Expected an HTTP(S) URL without embedded credentials.')
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    if port and port != (443 if parsed.scheme == 'https' else 80):
        host += ':' + str(port)
    origin = parsed.scheme + '://' + host
    # Routing metadata never stores query strings, fragments or credentials.
    return origin, urlunsplit((parsed.scheme, host, parsed.path or '/', '', ''))


def scope(environ):
    from .conversation import conversation
    from .settings import settings
    home = Path(environ.get('HERMES_HOME', '')).resolve()
    if not home.name.startswith('qq-') or home.parent.name != 'profiles':
        raise PermissionError('Use agent-browser from a trusted QQ conversation profile.')
    chat = conversation(home.name[3:])
    if not chat.permitted(settings()):
        raise PermissionError('Untrusted browser conversation.')
    session = environ.get('HERMES_SESSION_ID') or environ.get('HERMES_SESSION_KEY') or 'foreground'
    message = environ.get('HERMES_SESSION_MESSAGE_ID', '')
    identity = hashlib.sha256(f'{session}:{message}'.encode()).hexdigest()[:16]
    return chat, home, identity


def read_state(home):
    path = home / 'browser-state.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    if not path.exists():
        # Adopt existing profiles in place. Only retain origins, never history paths,
        # query strings, cookies or credentials; no Chromium startup is required.
        profiles = home / 'browser-profiles'
        origins = {}
        if profiles.exists():
            for profile in sorted(profiles.iterdir()):
                if not profile.is_dir() or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', profile.name):
                    continue
                for history in profile.glob('*/History'):
                    try:
                        with sqlite3.connect(f'file:{history}?mode=ro', uri=True, timeout=1) as db:
                            urls = db.execute('SELECT url FROM urls ORDER BY last_visit_time DESC LIMIT 100').fetchall()
                        for (url,) in urls:
                            try: origin, _ = website(url)
                            except ValueError: continue
                            origins.setdefault(origin, profile.name)
                    except sqlite3.Error:
                        continue
            if (profiles / 'personal').is_dir():
                state['lanes'] = {'foreground': {'mode': 'persistent', 'account': 'personal'}}
        state['origins'] = origins
    state.setdefault('origins', {})
    state.setdefault('lanes', {})
    return state


def save_state(home, state):
    home.mkdir(parents=True, exist_ok=True)
    path = home / 'browser-state.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temp.chmod(0o600)
    temp.replace(path)


def prepare(argv, environ, state=None):
    chat, home, identity = scope(environ)
    state = read_state(home) if state is None else state
    args = list(argv)
    job = ''; account = None; mode = None
    while args and args[0] in ('--job', '--account', '--persistent', '--temporary'):
        flag = args.pop(0)
        if flag in ('--job', '--account'):
            if not args:
                raise ValueError(flag + ' requires a name and command.')
            value = named(args.pop(0))
            if flag == '--job':
                if job: raise ValueError('Specify --job only once.')
                job = value
            else:
                if mode == 'temporary': raise ValueError('Conflicting browser modes.')
                account = value; mode = 'persistent'
        else:
            selected = 'persistent' if flag == '--persistent' else 'temporary'
            if mode and selected != mode: raise ValueError('Conflicting browser modes.')
            mode = selected
    for arg in args:
        if arg.split('=', 1)[0] in MANAGED_FLAGS:
            raise ValueError('Launch flags are managed. Use --account NAME, --temporary or --job NAME.')
    if not args:
        raise ValueError('Use browser-status, --help, or skills get core.')
    # Common mistaken grouping: console/errors are top-level native commands.
    if args[:2] in (['network', 'console'], ['network', 'errors']):
        args = args[1:]
    if args[0] in ('install', 'upgrade', 'dashboard', 'connect', 'auth', 'mcp', 'state', 'stream', 'inspect') or (args[0] == 'close' and '--all' in args):
        raise ValueError('Installation, remote control and credential export are not available here.')
    lane = 'job:' + job if job else 'foreground'
    previous = state['lanes'].get(lane, {})
    explicit = mode is not None
    target = None
    if args[0] == 'open' and len(args) > 1 and args[1].startswith(('https://', 'http://')):
        target = website(args[1])
    if args[0] == 'forget-account':
        if len(args) != 3 or args[2] != '--confirm':
            raise ValueError('Use forget-account NAME --confirm only on an explicit request to forget local login data.')
        account = named(args[1]); mode = 'persistent'; explicit = True
    if mode is None:
        if target and previous.get('turn') != identity:
            account = state['origins'].get(target[0])
            mode = 'persistent' if account else 'temporary'
        elif previous:
            mode = previous['mode']; account = previous.get('account')
        else:
            mode = 'temporary'
    if mode == 'persistent':
        account = named(account or previous.get('account') or 'personal')
        session = 'p-' + hashlib.sha256(account.encode()).hexdigest()[:16]
    else:
        account = None
        session = 't-' + hashlib.sha256((identity + ':' + lane).encode()).hexdigest()[:16]
    browser_home = home / 'cache' / 'agent-browser-home'
    workspace = chat.directory / 'workspace'
    downloads = workspace / 'browser-downloads'; screenshots = workspace / 'browser-screenshots'
    for directory in (browser_home, downloads, screenshots):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    env = {k: v for k, v in environ.items() if k in (
        'PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
        'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
    idle = PERSISTENT_IDLE_MS if account else IDLE_MS
    env.update(HOME=str(browser_home), XDG_CACHE_HOME=str(browser_home / '.cache'),
        XDG_CONFIG_HOME=str(browser_home / '.config'), AGENT_BROWSER_NAMESPACE=chat.profile,
        AGENT_BROWSER_SESSION=session, AGENT_BROWSER_SOCKET_DIR='/tmp/ab/' + chat.profile,
        AGENT_BROWSER_EXECUTABLE_PATH='/usr/bin/chromium', AGENT_BROWSER_IDLE_TIMEOUT_MS=str(idle),
        AGENT_BROWSER_DEFAULT_TIMEOUT='25000', AGENT_BROWSER_NO_WEBMCP='1',
        # Launch-level UA covers popup first requests and preserves native UA-CH.
        # AGENT_BROWSER_USER_AGENT uses a CDP override that clears client hints.
        AGENT_BROWSER_ARGS='--user-agent=' + user_agent(),
        AGENT_BROWSER_CONTENT_BOUNDARIES='1', AGENT_BROWSER_MAX_OUTPUT='16000',
        AGENT_BROWSER_DOWNLOAD_PATH=str(downloads), AGENT_BROWSER_SCREENSHOT_DIR=str(screenshots))
    if account:
        env['AGENT_BROWSER_PROFILE'] = str(home / 'browser-profiles' / account)
    owner = {'chat': chat.chat_id, 'session': session, 'home': str(browser_home),
        'profile_home': str(home), 'persistent': bool(account), 'account': account,
        'mode': mode, 'lane': lane, 'turn': identity, 'idle_ms': idle, 'explicit': explicit}
    if previous.get('account') == account:
        owner['login_until'] = previous.get('login_until', 0)
        owner['last_url'] = previous.get('last_url')
    if target:
        owner['target_origin'], owner['target_url'] = target
    return args, env, owner


def emit(**value):
    print(json.dumps(value, ensure_ascii=False))


def record(lock, value):
    lock.seek(0); lock.truncate()
    if value: json.dump(value, lock)
    lock.flush()


def close_previous(previous, env):
    old_home = Path(previous['home'])
    old_env = dict(env, HOME=str(old_home), XDG_CACHE_HOME=str(old_home / '.cache'),
        XDG_CONFIG_HOME=str(old_home / '.config'), AGENT_BROWSER_NAMESPACE='qq-' + previous['chat'],
        AGENT_BROWSER_SESSION=previous['session'], AGENT_BROWSER_SOCKET_DIR='/tmp/ab/qq-' + previous['chat'])
    old_env.pop('AGENT_BROWSER_PROFILE', None)
    return subprocess.run([BINARY, 'close'], env=old_env, capture_output=True, text=True, timeout=30).returncode == 0


def status(home, state, owner, lease):
    accounts = home / 'browser-profiles'
    selected = state['lanes'].get(owner['lane'], {})
    emit(accounts=sorted(p.name for p in accounts.iterdir() if p.is_dir()) if accounts.exists() else [],
        selection={k: selected.get(k) for k in ('mode', 'account', 'last_url')},
        login_wait_seconds=max(0, int(selected.get('login_until', 0) - time.time())),
        slot_owned_by_this_lane=bool(lease and lease.get('chat') == owner['chat'] and lease.get('lane') == owner['lane']),
        notice='Saved profiles do not prove a website is logged in. Inspect the page to verify.')


def run(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (['--help'], ['-h']):
        print(HELP, flush=True)
        return subprocess.run([BINARY, *args], timeout=30).returncode
    if args[:1] == ['--version'] or args[:2] in (['skills', 'list'], ['skills', 'get']):
        return subprocess.run([BINARY, *args], timeout=30).returncode
    _, home, _ = scope(os.environ)
    LEASE.parent.mkdir(parents=True, exist_ok=True)
    with LEASE.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state(home)
        args, env, owner = prepare(args, os.environ, state)
        lock.seek(0)
        try: previous = json.load(lock)
        except (ValueError, TypeError): previous = {}
        command = args[0]
        if command == 'browser-status':
            status(home, state, owner, previous)
            return 0
        if command == 'remember':
            if not owner['account'] or len(args) < 2:
                raise ValueError('Use --account NAME remember URL [URL ...].')
            origins = [website(url)[0] for url in args[1:]]
            for origin in origins: state['origins'][origin] = owner['account']
            save_state(home, state)
            emit(account=owner['account'], remembered_origins=origins)
            return 0
        if command == 'forget-account':
            in_use = previous.get('chat') == owner['chat'] and previous.get('account') == owner['account']
            if in_use:
                remaining = previous.get('idle_ms', IDLE_MS) / 1000 + 15 - (time.time() - previous.get('updated', 0))
                if previous.get('lane') != owner['lane'] and remaining > 0:
                    emit(error='browser_busy', notice='This account is in use by another job; stop that job before forgetting it.')
                    return 75
                if not close_previous(previous, env):
                    emit(error='browser_cleanup_failed'); return 75
            directory = home / 'browser-profiles' / owner['account']
            if directory.is_symlink(): raise ValueError('Refusing to remove a symlinked account directory.')
            if directory.exists(): shutil.rmtree(directory)
            state['origins'] = {k: v for k, v in state['origins'].items() if v != owner['account']}
            state['lanes'] = {k: v for k, v in state['lanes'].items() if v.get('account') != owner['account']}
            save_state(home, state)
            if in_use: record(lock, {})
            emit(forgotten_account=owner['account'], notice='Local browser data removed; this does not revoke website sessions elsewhere.')
            return 0
        same = all(previous.get(k) == owner.get(k) for k in ('chat', 'session', 'lane', 'persistent'))
        same_lane = previous.get('chat') == owner['chat'] and previous.get('lane') == owner['lane']
        if previous and not same:
            age = time.time() - previous.get('updated', 0)
            remaining = previous.get('idle_ms', IDLE_MS) / 1000 + 15 - age
            pending = previous.get('login_until', 0) > time.time()
            if remaining > 0 and (not same_lane or pending):
                emit(error='login_pending' if same_lane and pending else 'browser_busy',
                    retry_after_seconds=max(1, int(remaining)),
                    notice='A login is waiting: resume with no mode flags, or login-cancel first.' if same_lane and pending else
                    'Another browser job owns the slot. Continue non-browser work; do not sleep or poll in the foreground.')
                return 75
            if not close_previous(previous, env):
                emit(error='browser_cleanup_failed', notice='Previous browser could not be closed; slot remains reserved.')
                return 75
            record(lock, {})
        if command in ('login-wait', 'login-done') and not owner['account']:
            raise ValueError('Start login with --account NAME open URL. Temporary tabs cannot retain login data.')
        if command == 'login-wait':
            # Reading the URL also refreshes the native daemon's ten-minute idle timer.
            result = subprocess.run([BINARY, 'get', 'url'], env=env, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
            url = result.stdout.strip()
            if result.returncode or not url.startswith(('http://', 'https://')):
                emit(error='login_page_missing', notice='Open the intended login page in this account before login-wait.')
                return 2
            origin, safe_url = website(url)
            state['origins'][origin] = owner['account']
            owner['last_url'] = safe_url
            owner['login_until'] = time.time() + PERSISTENT_IDLE_MS / 1000
            owner['updated'] = time.time()
            state['lanes'][owner['lane']] = owner
            save_state(home, state); record(lock, owner)
            emit(account=owner['account'], login_wait_seconds=PERSISTENT_IDLE_MS // 1000,
                notice='Return the QR image and ask the user to reply when ready. Do not close, sleep or poll. QR validity is controlled by the website.')
            return 0
        if command == 'login-done':
            owner['login_until'] = 0
            state['lanes'][owner['lane']] = owner
            save_state(home, state)
            if same and previous:
                previous['login_until'] = 0; record(lock, previous)
            emit(account=owner['account'], notice='Login wait ended. Verify an authenticated page; close and reopen it before claiming persistence.')
            return 0
        closing = command in ('close', 'quit', 'exit', 'login-cancel')
        if owner['account']:
            Path(env['AGENT_BROWSER_PROFILE']).mkdir(parents=True, mode=0o700, exist_ok=True)
        owner['updated'] = time.time()
        record(lock, owner)
        try:
            if command in ('diagnose', 'checkpoint', 'resume'):
                from .browser_recovery import execute
            else:
                from .browser_forms import execute
            result = execute(BINARY, env, ['close' if closing else command, *args[1:]], COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            emit(error='browser_command_timeout', notice='Inspect the page before repeating a click or submission.')
            return 124
        finally:
            owner['updated'] = time.time()
            state['lanes'][owner['lane']] = owner
            save_state(home, state); record(lock, owner)
        if result.returncode == 0:
            if command == 'open' and owner.get('target_origin') and owner['account']:
                state['origins'][owner['target_origin']] = owner['account']
                owner['last_url'] = owner['target_url']
            if closing:
                owner['login_until'] = 0
                record(lock, {})
            state['lanes'][owner['lane']] = owner
            save_state(home, state)
        return result.returncode


def main():
    try:
        raise SystemExit(run())
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        emit(error=type(exc).__name__, notice=str(exc))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
