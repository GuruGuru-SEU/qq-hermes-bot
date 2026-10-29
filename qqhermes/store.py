"""Small durable transport ledger; Hermes owns agent sessions and cron jobs."""
from enum import StrEnum
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class TurnStatus(StrEnum):
    ACCEPTED = 'accepted'
    QUEUED = 'queued'
    PROCESSING = 'processing'
    SUCCESS = 'success'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    INTERRUPTED = 'interrupted'
    NOT_ACCEPTED = 'not_accepted'
    QUEUE_FULL = 'queue_full'


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Legacy INTEGER-affinity group_id also accepts dm-<uid> TEXT keys in SQLite.
        # Keep numeric group rows intact; DM keys cannot collide with them.
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS triggers (group_id INTEGER, message_id TEXT, status TEXT,
              updated REAL, PRIMARY KEY(group_id,message_id));
            CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, group_id INTEGER, user_id TEXT,
              message_id TEXT, args TEXT, status TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS members (group_id INTEGER, user_id TEXT, text TEXT,
              updated REAL, PRIMARY KEY(group_id,user_id));
            CREATE TABLE IF NOT EXISTS outbound_sequences (
              id TEXT PRIMARY KEY, chat TEXT, content TEXT, reply_to TEXT, parts TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS outbound_parts (
              sequence_id TEXT, position INTEGER, status TEXT, message_id TEXT,
              updated REAL, PRIMARY KEY(sequence_id,position));
            CREATE TABLE IF NOT EXISTS task_checkpoints (
              chat TEXT, message_id TEXT, user_id TEXT, state TEXT, updated REAL,
              PRIMARY KEY(chat,message_id));
            CREATE TABLE IF NOT EXISTS browser_steps (
              chat TEXT, message_id TEXT, count INTEGER,
              PRIMARY KEY(chat,message_id));
            CREATE TABLE IF NOT EXISTS inbox (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL,
              message_id TEXT NOT NULL, event TEXT NOT NULL, context TEXT NOT NULL,
              UNIQUE(group_id,message_id));
            CREATE TABLE IF NOT EXISTS children (id TEXT PRIMARY KEY, group_id INTEGER, user_id TEXT);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def claim(self, group, mid):
        with self.db() as db:
            return bool(db.execute('INSERT OR IGNORE INTO triggers VALUES (?,?,?,?)',
                                   (group,str(mid),'accepted',time.time())).rowcount)

    def finish(self, group, mid, status):
        status = TurnStatus.FAILED if status == 'failure' else TurnStatus(status)
        with self.db() as db:
            db.execute('UPDATE triggers SET status=?,updated=? WHERE group_id=? AND message_id=?',
                       (status,time.time(),group,str(mid)))
            if status not in (TurnStatus.QUEUED, TurnStatus.PROCESSING):
                db.execute('DELETE FROM inbox WHERE group_id=? AND message_id=?', (str(group), str(mid)))

    def enqueue(self, group, data, context):
        """Claim and persist arrival snapshot atomically, before acknowledging."""
        with self.db() as db:
            claimed = db.execute('INSERT OR IGNORE INTO triggers VALUES (?,?,?,?)',
                                 (group, str(data['message_id']), 'queued', time.time())).rowcount
            if not claimed:
                return False
            db.execute('INSERT INTO inbox(group_id,message_id,event,context) VALUES (?,?,?,?)',
                       (str(group), str(data['message_id']), json.dumps(data), json.dumps(context)))
            return True

    def recover_inbox(self):
        """Never replay a turn that could already have caused external effects."""
        with self.db() as db:
            db.execute("UPDATE triggers SET status='interrupted',updated=? WHERE status IN ('processing','accepted')",
                       (time.time(),))
            # Old builds did not persist queued envelopes. Mark these explicitly.
            db.execute("""UPDATE triggers SET status='interrupted',updated=?
                WHERE status='queued' AND NOT EXISTS (
                    SELECT 1 FROM inbox i WHERE i.group_id=CAST(triggers.group_id AS TEXT)
                    AND i.message_id=triggers.message_id)""", (time.time(),))
            db.execute("""DELETE FROM inbox WHERE EXISTS (SELECT 1 FROM triggers t
                WHERE CAST(t.group_id AS TEXT)=inbox.group_id AND t.message_id=inbox.message_id
                AND t.status!='queued')""")
            rows = db.execute("""SELECT i.event,i.context FROM inbox i JOIN triggers t
                ON CAST(t.group_id AS TEXT)=i.group_id AND t.message_id=i.message_id
                WHERE t.status='queued' ORDER BY i.sequence""").fetchall()
            return [(json.loads(r['event']), json.loads(r['context'])) for r in rows]

    def turn_context(self, group, mid, since):
        """Transport facts only; completion does not prove any requested side effect."""
        with self.db() as db:
            rows=db.execute('''SELECT message_id,status FROM triggers
                WHERE group_id=? AND message_id!=? AND updated>=?
                ORDER BY updated DESC LIMIT 16''',(group,str(mid),since)).fetchall()
        return {'current_message_id':str(mid),'delivery':'first_admission',
                'order':'gateway_arrival; QQ timestamps may tie',
                'earlier_turns':[{ 'message_id':r['message_id'],'outcome':r['status']}
                                for r in reversed(rows) if r['status'] not in ('queued','processing','accepted')][-8:],
                'other_pending_message_ids':[r['message_id'] for r in reversed(rows)
                                             if r['status'] in ('queued','processing')]}

    def proposal(self, group, user, mid, args):
        raw=json.dumps(args,sort_keys=True,ensure_ascii=False)
        identity=hashlib.sha256(f'{group}:{user}:{mid}:{raw}'.encode()).hexdigest()[:12]
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?,?,?,?,?,?)',
                       (identity,group,str(user),str(mid),raw,'pending',time.time()))
        return identity

    def approve(self, identity, group, user, mid):
        with self.db() as db:
            row=db.execute('SELECT * FROM proposals WHERE id=?',(identity,)).fetchone()
            if not row or row['group_id']!=group or row['user_id']!=str(user) or row['message_id']==str(mid):
                raise PermissionError('Only the original requester can confirm in a later message')
            if row['status']!='pending' or time.time()-row['created']>86400:
                raise ValueError('Proposal expired or already handled')
            db.execute("UPDATE proposals SET status='approved',message_id=? WHERE id=?",(str(mid),identity))
            return json.loads(row['args'])

    def consume(self, group, user, mid, args):
        raw=json.dumps(args,sort_keys=True,ensure_ascii=False)
        with self.db() as db:
            row=db.execute("SELECT id FROM proposals WHERE group_id=? AND user_id=? AND message_id=? AND args=? AND status='approved'",
                           (group,str(user),str(mid),raw)).fetchone()
            if not row: return False
            db.execute("UPDATE proposals SET status='launched' WHERE id=?",(row['id'],))
            return True

    def outgoing(self, identity):
        with self.db() as db:
            row = db.execute('SELECT * FROM outbound_sequences WHERE id=?', (identity,)).fetchone()
            if row is None: return None
            value = dict(row)
            value['parts'] = json.loads(value['parts'])
            value['receipts'] = [dict(r) for r in db.execute(
                'SELECT * FROM outbound_parts WHERE sequence_id=? ORDER BY position', (identity,))]
            return value

    def prepare_outgoing(self, identity, chat, content, reply_to, parts):
        with self.db() as db:
            inserted = db.execute('INSERT OR IGNORE INTO outbound_sequences VALUES (?,?,?,?,?,?)',
                (identity, str(chat), content, reply_to, json.dumps(parts, ensure_ascii=False), time.time())).rowcount
            if inserted:
                db.executemany('INSERT INTO outbound_parts VALUES (?,?,?,?,?)',
                    [(identity, i, 'pending', None, time.time()) for i in range(len(parts))])
        return self.outgoing(identity)

    def outgoing_status(self, identity, position, status, message_id=None):
        with self.db() as db:
            db.execute('UPDATE outbound_parts SET status=?,message_id=?,updated=? WHERE sequence_id=? AND position=?',
                (status, message_id, time.time(), identity, position))

    def coalesce_outgoing_tail(self, identity):
        """Upgrade an old bubble plan: keep receipts, combine only never-attempted suffix."""
        with self.db() as db:
            row=db.execute('SELECT parts FROM outbound_sequences WHERE id=?',(identity,)).fetchone()
            if row is None:return
            parts=json.loads(row['parts'])
            receipts=db.execute('SELECT position,status FROM outbound_parts WHERE sequence_id=? ORDER BY position',(identity,)).fetchall()
            start=next((i for i,r in enumerate(receipts) if r['status']!='sent'),len(receipts))
            if len(parts)-start<2 or any(r['status']!='pending' for r in receipts[start:]):return
            parts=parts[:start]+[''.join(parts[start:])]
            db.execute('UPDATE outbound_sequences SET parts=? WHERE id=?',(json.dumps(parts,ensure_ascii=False),identity))
            db.execute('DELETE FROM outbound_parts WHERE sequence_id=? AND position>?',(identity,start))
