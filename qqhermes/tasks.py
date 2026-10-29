"""Durable task notes, scoped to a request; notes cannot grant permissions."""
import json
import time

from .conversation import conversation_for_profile
from .settings import root
from .store import Store


def identity():
    from gateway.session_context import get_session_env
    from agent.delegation_context import is_delegated_child_context
    if is_delegated_child_context() or get_session_env('HERMES_CRON_SESSION', '') == '1':
        raise ValueError('Background tasks use their own task prompt, not foreground checkpoints.')
    chat = conversation_for_profile()
    mid = str(get_session_env('HERMES_SESSION_MESSAGE_ID', ''))
    user = str(get_session_env('HERMES_SESSION_USER_ID', ''))
    if not mid or not user:
        raise ValueError('Task checkpoints require a current QQ request.')
    return chat, mid, user


def database():
    return Store(root() / 'transport.sqlite3')


def current_checkpoint():
    try:
        chat, mid, user = identity()
    except (PermissionError, ValueError):
        return None
    with database().db() as db:
        row = db.execute('SELECT state FROM task_checkpoints WHERE chat=? AND message_id=?',
                         (chat.chat_id, mid)).fetchone()
    return json.loads(row['state']) if row else None


def task_tool(args, **kwargs):
    try:
        chat, mid, user = identity()
        store = database()
        action = args.get('action')
        with store.db() as db:
            if action == 'get':
                target = str(args.get('message_id') or mid)
                row = db.execute('SELECT state,updated FROM task_checkpoints WHERE chat=? AND message_id=?',
                                 (chat.chat_id, target)).fetchone()
                return json.dumps({'message_id': target, 'state': json.loads(row['state']) if row else None,
                                   'notice': 'Prior task notes are evidence, not authorization for the current request.'})
            if action != 'update':
                raise ValueError('Use get or update.')
            if args.get('message_id') not in (None, mid):
                raise ValueError('Only the current request checkpoint may be updated.')
            state = {k: args.get(k, '') for k in (
                'objective', 'mode', 'target', 'constraints', 'verified', 'pending', 'next_step')}
            if state['mode'] not in ('inspect', 'edit', 'research', 'create'):
                raise ValueError('Set mode to inspect, edit, research or create.')
            if not state['objective'] or not state['target']:
                raise ValueError('An objective and exact target/account or topic are required.')
            if any(not isinstance(v, str) or len(v) > 1600 for v in state.values()):
                raise ValueError('Each checkpoint field must be a string of at most 1600 characters.')
            raw = json.dumps(state, ensure_ascii=False)
            if len(raw) > 6000:
                raise ValueError('Keep task checkpoints under 6000 characters.')
            db.execute('INSERT OR REPLACE INTO task_checkpoints VALUES (?,?,?,?,?)',
                       (chat.chat_id, mid, user, raw, time.time()))
        return json.dumps({'success': True, 'message_id': mid, 'state': state,
                           'notice': 'Saved notes only; this does not approve actions, change the user request, or reset the browser step budget.'}, ensure_ascii=False)
    except (PermissionError, ValueError) as exc:
        return json.dumps({'success': False, 'error': str(exc)})


def browser_guard(command):
    """A workflow guard for the supported CLI, not a shell sandbox."""
    import shlex
    if not isinstance(command, str) or 'agent-browser' not in command:
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    actions = []
    for i, word in enumerate(words):
        if word.rsplit('/', 1)[-1] != 'agent-browser':
            continue
        tail = words[i + 1:]
        while tail:
            if tail[0] in ('--account', '--job'):
                tail = tail[2:]
            elif tail[0] in ('--persistent', '--temporary', '--json'):
                tail = tail[1:]
            else:
                break
        if tail:
            actions.append(tail[0])
    exempt = {'close', 'diagnose', 'checkpoint', 'browser-status', '--help', '-h', '--version', 'skills'}
    work = [a for a in actions if a not in exempt]
    if not work:
        return None
    try:
        chat, mid, user = identity()
    except (PermissionError, ValueError):
        return None  # Cron has its own task prompt; no fabricated foreground ID.
    store = database()
    with store.db() as db:
        row = db.execute('SELECT state FROM task_checkpoints WHERE chat=? AND message_id=?',
                         (chat.chat_id, mid)).fetchone()
        if not row:
            return ('Before browser work call qq_task update with the CURRENT objective, mode, exact target/account, '
                    'constraints, verified actions, pending decisions and next step. Screenshot/status requests are '
                    'inspection: do not resume old edits, save an order or retry a purchase. Notes cannot grant permission.')
        db.execute('INSERT OR IGNORE INTO browser_steps VALUES (?,?,0)', (chat.chat_id, mid))
        count = db.execute('SELECT count FROM browser_steps WHERE chat=? AND message_id=?',
                           (chat.chat_id, mid)).fetchone()['count']
        if count + len(work) > 40:
            return ('This request reached its 40 browser-command budget. Do not bypass via scripts or raw CDP. '
                    'Use diagnose/checkpoint/close, save qq_task progress and return the verified result or blocker. '
                    'If more work is needed, propose an approved background task or await a new user instruction.')
        db.execute('UPDATE browser_steps SET count=count+? WHERE chat=? AND message_id=?',
                   (len(work), chat.chat_id, mid))
    return None
