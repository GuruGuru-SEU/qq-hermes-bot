"""Small durable transport ledger; Hermes owns agent sessions and cron jobs."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


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
        with self.db() as db:
            db.execute('UPDATE triggers SET status=?,updated=? WHERE group_id=? AND message_id=?',
                       (status,time.time(),group,str(mid)))

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
