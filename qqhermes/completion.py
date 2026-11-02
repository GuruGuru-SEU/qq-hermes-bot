"""Receipt-backed task settlement and atomic foreground turn completion.

Delivery succeeds first; only then can a task settle. The loop can close once
every addressed task is settled. Messages arriving after that commit stay queued.
No model-generated silence token participates in this protocol.
"""
import json
import time


def settle(db, chat, root_mid, task_id, report_id, outcome='completed'):
    if outcome not in ('completed', 'waiting'):
        raise ValueError('A finished task must be completed or waiting for user input')
    receipt = db.execute("""SELECT 1 FROM work_reports WHERE id=? AND chat=?
        AND root_mid=? AND target=? AND status='sent'""",
        (report_id, str(chat), root_mid, task_id)).fetchone()
    associated = db.execute('SELECT 1 FROM live_requests WHERE chat=? AND root_mid=? AND target=?',
                            (str(chat), root_mid, task_id)).fetchone()
    item = db.execute('SELECT state FROM work_items WHERE chat=? AND mid=?', (str(chat), task_id)).fetchone()
    if not receipt or not associated or item is None:
        raise ValueError('Completion requires a confirmed report for this task in this turn')
    if item['state'] not in ('pending', 'working', 'completed', 'waiting'):
        raise ValueError('Cancelled or superseded work cannot be completed')
    prior = db.execute('SELECT report_id,outcome FROM task_completions WHERE chat=? AND root_mid=? AND task_id=?',
                       (str(chat), root_mid, task_id)).fetchone()
    if prior and (prior['report_id'], prior['outcome']) != (report_id, outcome):
        raise ValueError('Task already has a completion receipt; do not complete it twice')
    db.execute('INSERT OR IGNORE INTO task_completions VALUES (?,?,?,?,?,?)',
               (str(chat), root_mid, task_id, report_id, outcome, time.time()))
    db.execute('UPDATE work_items SET state=?,updated=? WHERE chat=? AND mid=?',
               (outcome, time.time(), str(chat), task_id))


def settled_receipts(db, chat, root_mid):
    """Return evidence for a settled turn, or None while any obligation is open."""
    requests = db.execute('SELECT relation,target FROM live_requests WHERE chat=? AND root_mid=?',
                          (str(chat), root_mid)).fetchall()
    if not requests or any(not r['relation'] for r in requests):
        return None
    targets = {r['target'] for r in requests if r['target']}
    receipts = []
    for task_id in sorted(targets):
        task = db.execute('SELECT state FROM work_items WHERE chat=? AND mid=?', (str(chat), task_id)).fetchone()
        if task is None or task['state'] in ('pending', 'working'):
            return None
        if task['state'] in ('cancelled', 'superseded'):
            continue
        if task['state'] not in ('completed', 'waiting'):
            return None
        receipt = db.execute("""SELECT r.id,r.text FROM task_completions c JOIN work_reports r ON r.id=c.report_id
            WHERE c.chat=? AND c.root_mid=? AND c.task_id=? AND c.outcome=? AND r.status='sent'
            AND r.chat=c.chat AND r.root_mid=c.root_mid AND r.target=c.task_id""",
            (str(chat), root_mid, task_id, task['state'])).fetchone()
        if receipt is None:
            return None
        receipts.append(dict(receipt))
    return receipts or None


def seal_turn(store, chat, root_mid):
    """Commit the delivery boundary under the same SQLite lock as admission."""
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT receipts FROM turn_completions WHERE chat=? AND root_mid=?',
                              (str(chat), root_mid)).fetchone()
        if existing:
            return json.loads(existing['receipts'])
        active = db.execute("SELECT 1 FROM triggers WHERE group_id=? AND message_id=? AND status='processing'",
                            (str(chat), root_mid)).fetchone()
        if not active:
            return None
        # A queued command/voice is a FIFO barrier, not an input for this agent.
        # Ordinary pending messages must be consumed before closing this turn.
        from .live import LiveInbox
        if LiveInbox(store).pending(db, chat, root_mid):
            return None
        receipts = settled_receipts(db, chat, root_mid)
        if not receipts:
            return None
        db.execute('INSERT INTO turn_completions VALUES (?,?,?,?)',
                   (str(chat), root_mid, json.dumps(receipts, ensure_ascii=False), time.time()))
        return receipts


def committed(store, chat, root_mid, receipt_ids):
    with store.db() as db:
        row = db.execute('SELECT receipts FROM turn_completions WHERE chat=? AND root_mid=?',
                         (str(chat), root_mid)).fetchone()
        if not row:
            return False
        stored = json.loads(row['receipts'])
        return bool(receipt_ids and receipt_ids == [r['id'] for r in stored]
                    and all(db.execute("SELECT 1 FROM work_reports WHERE id=? AND chat=? AND root_mid=? AND status='sent'",
                            (r['id'], str(chat), root_mid)).fetchone() for r in stored))
