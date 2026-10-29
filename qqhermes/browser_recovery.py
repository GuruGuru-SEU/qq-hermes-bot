"""Exact navigation checkpoints and bounded, read-only browser diagnostics."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit

from .browser_forms import FormError, command_data, evaluate


def route(value):
    """Display metadata only; never use a sanitized URL for navigation."""
    if not isinstance(value, str) or not value:
        return '[unavailable]'
    try:
        p = urlsplit(value)
        return urlunsplit((p.scheme, p.hostname or '', p.path, '', ''))
    except (ValueError, TypeError):
        return '[invalid URL]'


def scrub(value, limit=600):
    from agent.redact import redact_sensitive_text
    # Drop URL query/fragment values even when they aren't named like credentials.
    text = re.sub(r'https?://[^\s"<>]+', lambda m: route(m.group()), str(value))
    text = redact_sensitive_text(text, force=True, redact_url_credentials=True)
    return text[:limit] + (' [truncated]' if len(text) > limit else '')


def checkpoint_path(env, name):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', name):
        raise FormError('Checkpoint name must be 1-40 letters, digits, underscores or hyphens.')
    # HOME and session are set by the scoped launcher, not arbitrary CLI flags.
    session = hashlib.sha256(env['AGENT_BROWSER_SESSION'].encode()).hexdigest()[:20]
    directory = Path(env['HOME']) / 'navigation' / session
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    return directory / (name + '.json')


def exact_url(value):
    if not isinstance(value, str) or len(value) > 32768 or any(c.isspace() for c in value):
        raise FormError('Browser returned an invalid URL; no navigation performed.')
    try:
        p = urlsplit(value)
        valid = p.scheme in ('http', 'https') and p.hostname and not p.username and not p.password
    except ValueError:
        valid = False
    if not valid:
        raise FormError('Only observed HTTP(S) page URLs can be checkpointed.')
    return value


def checkpoint(binary, env, name, timeout):
    path = checkpoint_path(env, name)
    url = exact_url(command_data(binary, env, ['get', 'url'], time.monotonic()+timeout).get('url'))
    payload = {'url': url, 'saved_at': time.time(), 'session': env['AGENT_BROWSER_SESSION']}
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(payload, stream)
    tmp.replace(path)
    return {'checkpoint': name, 'route': route(url), 'exact_url_saved': True,
            'notice': 'Full query and fragment saved privately. Use resume NAME, never reconstruct this display route. Saving does not verify the page or complete an action.'}


def resume(binary, env, name, timeout):
    path = checkpoint_path(env, name)
    if path.is_symlink() or not path.is_file():
        raise FormError('No checkpoint in this browser account/session.')
    payload = json.loads(path.read_text())
    if payload.get('session') != env['AGENT_BROWSER_SESSION'] or time.time()-payload.get('saved_at', 0) > 86400:
        raise FormError('Checkpoint is stale or belongs to another browser; inspect current tabs and the original workflow.')
    url = exact_url(payload.get('url'))
    # Capture output so native open does not print token-bearing URLs. Never retry.
    deadline = time.monotonic()+timeout
    current = command_data(binary, env, ['get', 'url'], deadline).get('url')
    action = ['reload'] if current == url else ['open', url]
    data = command_data(binary, env, action, deadline)
    return {'checkpoint': name, 'navigation_requested': True, 'route': route(data.get('url', url)),
            'action': action[0],
            'notice': 'Opened the exact saved URL once. Verify current file/account/parameters and application state. This is not proof of a completed transaction.'}


def diagnose(binary, env, timeout):
    deadline = time.monotonic()+timeout
    report = {'observed_at': int(time.time()), 'read_only': True, 'collection_errors': []}
    def collect(name, args):
        try:
            return command_data(binary, env, args, deadline)
        except (FormError, subprocess.TimeoutExpired):
            report['collection_errors'].append(name)
            return {}
    try:
        page = evaluate(binary, env, """(()=>({
url:location.href,title:document.title,ready:document.readyState,
visibleCharacters:(document.body?.innerText||'').trim().length,
elements:document.getElementsByTagName('*').length,
frames:document.querySelectorAll('iframe').length,opener:!!window.opener,
resources:performance.getEntriesByType('resource').length,
userAgent:navigator.userAgent,userAgentData:navigator.userAgentData?.toJSON()||null
}))()""", deadline)
        report['page'] = {k: v for k, v in page.items() if k not in ('url', 'title')}
        report['page'].update(route=route(page.get('url', '')), title=scrub(page.get('title', ''), 160))
    except (FormError, subprocess.TimeoutExpired, AttributeError):
        report['collection_errors'].append('page')
    errors = collect('errors', ['errors']).get('errors', [])
    report['errors'] = [{'text': scrub(e.get('text', '')), 'line': e.get('line'),
                         'column': e.get('column'), 'route': route(e.get('url', ''))} for e in errors[-8:]]
    logs = collect('console', ['console']).get('messages', [])
    warnings = [m for m in logs if m.get('type') in ('error', 'warning', 'warn')]
    report['console'] = [{'type': m.get('type'), 'text': scrub(m.get('text', ''))} for m in warnings[-8:]]
    requests = collect('network', ['network', 'requests']).get('requests', [])
    relevant = [r for r in requests if r.get('resourceType') in ('Document', 'XHR', 'Fetch', 'Script')]
    failures = [r for r in relevant if r.get('failed') or r.get('error') or
                (isinstance(r.get('status'), int) and r['status'] >= 400)]
    def brief(r):
        return {'request_id': r.get('requestId'), 'method': r.get('method'),
                'type': r.get('resourceType'), 'status': r.get('status'),
                'timestamp': r.get('timestamp'), 'route': route(r.get('url', ''))}
    report.update(recent_requests=[brief(r) for r in relevant[-12:]],
                  failed_requests=[brief(r) for r in failures[-8:]])
    report['notice'] = ('Browser errors and network records may include older tabs; correlate them with request times. '
        'HTTP 200 and readyState=complete do not prove application success. No credentials, request/response bodies, '
        'or URL parameters are included. If a handoff failed, preserve its exact checkpoint and original file/source '
        'association; do not substitute a different upload workflow or infer eligibility from the fallback.')
    return report


def execute(binary, env, args, timeout):
    command = args[0]
    if command == 'diagnose' and len(args) == 1:
        value = diagnose(binary, env, timeout)
    elif command in ('checkpoint', 'resume') and len(args) == 2:
        value = (checkpoint if command == 'checkpoint' else resume)(binary, env, args[1], timeout)
    else:
        raise FormError('Use diagnose, checkpoint NAME, or resume NAME.')
    print(json.dumps(value, ensure_ascii=False))
    return subprocess.CompletedProcess(args, 0)
