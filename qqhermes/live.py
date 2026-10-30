"""Durable, sender-attributed follow-ups consumed only at model boundaries.

Task interpretation is the agent's job. Sender identity, cancellation/approval
ownership, report receipts, and admission/recovery are enforced here.
"""
import json
import time

from .conversation import conversation, conversation_for_profile
from .history import History
from .settings import root
from .store import Store

MAX_BATCH = 16


def text_of(event):
    return ''.join(str(s.get('data', {}).get('text', '')) for s in event.get('message', [])
                   if s.get('type') == 'text').strip()


def command(event):
    return text_of(event).startswith('/')


def scope():
    from gateway.session_context import get_session_env
    from agent.delegation_context import is_delegated_child_context
    if is_delegated_child_context() or get_session_env('HERMES_CRON_SESSION', '') == '1':
        return None
    if get_session_env('HERMES_SESSION_PLATFORM', '') != 'qq_onebot':
        return None
    try:
        chat = conversation_for_profile()
    except PermissionError:
        return None
    mid = str(get_session_env('HERMES_SESSION_MESSAGE_ID', ''))
    if not mid or str(get_session_env('HERMES_SESSION_CHAT_ID', '')) != chat.chat_id:
        return None
    return chat, mid


class LiveInbox:
    def __init__(self, store):
        self.store = store

    def start(self, chat, data):
        mid = str(data['message_id'])
        with self.store.db() as db:
            self._record(db, chat, mid, data, 'new', mid)
            db.execute('INSERT OR IGNORE INTO work_items VALUES (?,?,?,?,?,?)',
                       (str(chat), mid, str(data['user_id']), text_of(data)[:2000], 'working', time.time()))

    def _record(self, db, chat, root_mid, data, relation='', target=''):
        db.execute('INSERT OR IGNORE INTO live_requests VALUES (?,?,?,?,?,?,?)',
                   (str(chat), str(data['message_id']), str(root_mid), str(data['user_id']),
                    json.dumps(data, ensure_ascii=False), relation, target))

    def active(self, db, chat, mid):
        return db.execute("SELECT 1 FROM live_requests l JOIN triggers t ON t.group_id=l.chat AND t.message_id=l.mid WHERE l.chat=? AND l.mid=? AND l.root_mid=l.mid AND t.status='processing'",
                          (str(chat), str(mid))).fetchone() is not None

    def pending(self, db, chat, mid):
        if db.execute('SELECT COUNT(*) FROM live_requests WHERE chat=? AND root_mid=?', (str(chat), mid)).fetchone()[0] >= 100:
            return []
        # Slash commands are barriers: /new and /approve keep their original
        # sender/session semantics and are never interpreted as ordinary prose.
        rows = db.execute("SELECT i.event FROM inbox i JOIN triggers t ON t.group_id=i.group_id AND t.message_id=i.message_id WHERE i.group_id=? AND t.status='queued' ORDER BY i.sequence LIMIT ?",
                          (str(chat), MAX_BATCH)).fetchall()
        result = []
        for row in rows:
            data = json.loads(row['event'])
            # Voice needs the adapter's asynchronous transcription stage. Keep
            # it in FIFO rather than consuming an unreadable audio placeholder.
            if command(data) or any(s.get('type') in ('record', 'audio', 'ptt') for s in data.get('message', [])):
                break
            result.append(data)
        return result

    def take(self, chat, mid):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self.active(db, chat, mid):
                return []
            count = db.execute('SELECT COUNT(*) FROM live_requests WHERE chat=? AND root_mid=?', (str(chat), mid)).fetchone()[0]
            if count >= 100:
                return []  # Bound one agent run; the remainder retains FIFO admission.
            data = self.pending(db, chat, mid)
            data = data[:100-count]
            for event in data:
                self._record(db, chat, mid, event)
                db.execute("UPDATE triggers SET status='processing',updated=? WHERE group_id=? AND message_id=? AND status='queued'",
                           (time.time(), str(chat), str(event['message_id'])))
            return data

    def state(self, chat, mid):
        with self.store.db() as db:
            requests = [dict(r) for r in db.execute('SELECT mid,owner,relation,target FROM live_requests WHERE chat=? AND root_mid=? ORDER BY rowid', (str(chat), mid))]
            items = [dict(r) for r in db.execute('''SELECT mid,owner,objective,state FROM work_items
                WHERE chat=? AND (mid IN (SELECT target FROM live_requests WHERE chat=? AND root_mid=?)
                  OR mid IN (SELECT mid FROM work_items WHERE chat=? ORDER BY updated DESC LIMIT 10))
                ORDER BY updated''', (str(chat), str(chat), mid, str(chat)))]
            reports = [dict(r) for r in db.execute('SELECT target,source,kind,status,outcome FROM work_reports WHERE chat=? AND root_mid=? ORDER BY updated', (str(chat), mid))]
            recent_targets = {r['mid'] for r in items}
            proposals = []
            for row in db.execute("""SELECT l.mid,l.owner,l.target,l.event FROM live_requests l
                WHERE l.chat=? AND l.relation='proposal' AND NOT EXISTS
                (SELECT 1 FROM work_approvals a WHERE a.chat=l.chat AND a.proposal_mid=l.mid)
                ORDER BY l.rowid DESC LIMIT 30""", (str(chat),)):
                if row['target'] in recent_targets:
                    proposals.append({'message_id':row['mid'], 'sender_id':row['owner'],
                                      'task_id':row['target'], 'text':text_of(json.loads(row['event']))[:2000]})
        targets = {r['target'] for r in requests if r['target']}
        return {'requests': requests, 'tasks': items, 'reports': reports, 'pending_proposals': proposals,
                'open_tasks': [r for r in items if r['mid'] in targets and r['state'] in ('pending', 'working')],
                'unclassified': [r['mid'] for r in requests if not r['relation']]}

    def resolve(self, chat, mid, args):
        source, target = str(args['message_id']), str(args.get('task_id') or mid)
        relation = args['relation']
        if relation == 'new': target = source
        allowed = {'new', 'refine', 'contribute', 'proposal', 'approve', 'cancel', 'replace', 'question', 'attachment'}
        if relation not in allowed:
            raise ValueError('Invalid relation')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM live_requests WHERE chat=? AND mid=? AND root_mid=?', (str(chat), source, mid)).fetchone()
            if row is None:
                raise ValueError('Source message must belong to this active turn')
            if row['relation'] == relation and row['target'] == (source if relation == 'replace' else target):
                return self.state(chat, mid)
            item = db.execute('SELECT * FROM work_items WHERE chat=? AND mid=?', (str(chat), target)).fetchone()
            if relation != 'new' and item is None:
                raise ValueError('Unknown target task')
            if relation in {'refine', 'approve', 'cancel', 'replace'} and row['owner'] != item['owner']:
                raise PermissionError('Only the task requester may approve, override, replace or cancel it; record another member’s idea as contribute or proposal')
            if relation == 'approve':
                proposal = db.execute("SELECT * FROM live_requests WHERE chat=? AND mid=? AND target=? AND relation='proposal'",
                                      (str(chat), str(args.get('proposal_id', '')), target)).fetchone()
                if proposal is None or proposal['mid'] == source:
                    raise ValueError('Approval must reference an earlier recorded proposal')
                if float(json.loads(proposal['event']).get('time', 0)) > float(json.loads(row['event']).get('time', 0)):
                    raise ValueError('Approval must follow the proposal')
                db.execute('INSERT OR REPLACE INTO work_approvals VALUES (?,?,?,?)',
                           (str(chat),source,proposal['mid'],target))
            if row['relation'] and (row['relation'], row['target']) != (relation, target):
                # The initial request may refer to an older task; other decisions
                # cannot be silently reinterpreted after side effects/delivery.
                if source != mid or row['relation'] != 'new':
                    raise ValueError('Message already classified; preserve its decision')
                db.execute("UPDATE work_items SET state='superseded',updated=? WHERE chat=? AND mid=?", (time.time(), str(chat), mid))
            if relation in {'new', 'replace'}:
                if relation == 'replace':
                    db.execute("UPDATE work_items SET state='superseded',updated=? WHERE chat=? AND mid=?", (time.time(), str(chat), target))
                target = source
                objective = str(args.get('objective') or text_of(json.loads(row['event'])))[:2000]
                db.execute('INSERT INTO work_items VALUES (?,?,?,?,?,?) ON CONFLICT(chat,mid) DO UPDATE SET objective=excluded.objective,state=excluded.state,updated=excluded.updated',
                           (str(chat), source, row['owner'], objective, 'working', time.time()))
            elif relation == 'cancel':
                db.execute("UPDATE work_items SET state='cancelled',updated=? WHERE chat=? AND mid=?", (time.time(), str(chat), target))
            elif relation in {'refine', 'approve'}:
                db.execute("UPDATE work_items SET state='working',updated=? WHERE chat=? AND mid=?", (time.time(), str(chat), target))
                if args.get('objective'):
                    db.execute('UPDATE work_items SET objective=? WHERE chat=? AND mid=?', (str(args['objective'])[:2000], str(chat), target))
            db.execute('UPDATE live_requests SET relation=?,target=? WHERE chat=? AND mid=?', (relation, target, str(chat), source))
        return self.state(chat, mid)

    def finish(self, chat, mid, outcome):
        with self.store.db() as db:
            ids = [r[0] for r in db.execute('SELECT mid FROM live_requests WHERE chat=? AND root_mid=?', (str(chat), mid))]
            db.execute("""UPDATE work_items SET state=?,updated=? WHERE chat=?
                AND state IN ('pending','working') AND mid IN
                (SELECT target FROM live_requests WHERE chat=? AND root_mid=?)""",
                ('waiting' if outcome == 'success' else str(outcome), time.time(), str(chat), str(chat), mid))
        for identity in ids:
            self.store.finish(chat, identity, outcome)
        return ids


def describe(chat, events):
    archive = History(chat.directory / 'history.sqlite3')
    result = []
    for data in events:
        mid = str(data['message_id'])
        from .context import attachments, ContextWindow
        from .mentions import incoming_mentions
        # Apply the same arrival-time image association to live follow-ups as
        # to an initial turn. Ordinary group images never enter the work inbox.
        rows = [r for r in archive.recent(before=float(data.get('time', time.time())), limit=31)
                if r['message_id'] != mid]
        _, media_ids, reference = ContextWindow().select(archive, data, rows, text_of(data))
        quoted = [str(s['data'].get('id')) for s in data['message'] if s['type'] == 'reply']
        reply_tasks = []
        for quoted_id in quoted:
            reply_tasks.extend(str(s['data']['id']) for s in archive.segments(quoted_id)
                               if s.get('type') == 'reply' and s.get('data', {}).get('id'))
        result.append({'message_id': mid, 'sender_id': str(data['user_id']),
                       'sender_name': data.get('sender', {}).get('card') or data.get('sender', {}).get('nickname', ''),
                       'timestamp': data.get('time'), 'text': (text_of(data) or archive.message_text(mid))[:6000],
                       'reply_to': quoted, 'reply_task_ids': reply_tasks,
                       'mention_user_ids': incoming_mentions(data),
                       'attachments': attachments(archive, mid),
                       'referenced_attachments': [{'message_id': target, **attachments(archive, target)}
                                                  for target in media_ids if target != mid],
                       'image_reference': reference})
    return result


def boundary(agent, messages, *, final=False):
    """Pinned native loop calls this before requests and before executing responses.

    No model reviewer. If new input arrived during generation, discard that
    unexecuted response and let the SAME agent incorporate the new user rows.
    """
    identity = scope()
    if identity is None:
        return False
    chat, mid = identity
    inbox = LiveInbox(Store(root() / 'transport.sqlite3'))
    events = inbox.take(chat.key, mid)
    if events:
        payload = {'incoming_messages': describe(chat, events), 'work': inbox.state(chat.key, mid)}
        from agent.prompt_builder import steer_user_row
        messages.append(steer_user_row('QQ live inbox: each sender has their OWN authority, not the original requester’s. '
            'Classify each message with qq_work resolve. Compatible contributions may be used; major/conflicting changes need the task owner’s approval. '
            'Attachments are archived: qq_image/qq_file retrieves them. Interpret latest constraints before further actions; no reply is owed per message.\n' + json.dumps(payload, ensure_ascii=False)))
        return True
    if final:
        state = inbox.state(chat.key, mid)
        if state['unclassified'] or len(state['open_tasks']) > 1:
            attempts = getattr(agent, '_qq_inbox_finish_nudges', 0)
            if attempts < 3:
                agent._qq_inbox_finish_nudges = attempts + 1
                from agent.prompt_builder import steer_user_row
                messages.append(steer_user_row('QQ work is not accounted for yet. Resolve unclassified messages; deliver separate completed tasks using qq_work report(kind=result), then give the last result as the final answer. Do not repeat already delivered results.\n' + json.dumps(state, ensure_ascii=False)))
                return True
    return False


def work_tool(args, **kwargs):
    try:
        identity = scope()
        if identity is None:
            raise PermissionError('qq_work requires an active foreground QQ turn')
        chat, mid = identity
        inbox = LiveInbox(Store(root() / 'transport.sqlite3'))
        action = args.get('action')
        if action == 'list':
            result = inbox.state(chat.key, mid)
        elif action == 'resolve':
            result = inbox.resolve(chat.key, mid, args)
        elif action == 'report':
            import os
            import httpx
            response = httpx.post('http://127.0.0.1:8080/internal/work-report',
                headers={'Authorization': 'Bearer ' + os.environ['ONEBOT_TOKEN']},
                json={**args, 'chat_id': chat.chat_id, 'root_mid': mid}, timeout=60)
            result = response.json()
        else:
            raise ValueError('Use list, resolve or report')
        return json.dumps(result, ensure_ascii=False)
    except (ValueError, KeyError, PermissionError) as exc:
        return json.dumps({'success': False, 'error': str(exc)}, ensure_ascii=False)
