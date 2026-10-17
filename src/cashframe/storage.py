from pathlib import Path
from contextlib import contextmanager
import json
import sqlite3
import time
import uuid


class JobStore:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path,timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self):
        jid=uuid.uuid4().hex
        created=time.time()
        payload={'job_id':jid,'status':'processing','stage':'accepted','created_at':created}
        with self.connect() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?)',(jid,created,json.dumps(payload)))
        return jid

    def get(self,jid):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM jobs WHERE id=?',(jid,)).fetchone()
        return json.loads(row[0]) if row else None

    def update(self,jid,**fields):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM jobs WHERE id=?',(jid,)).fetchone()
            if row is None:raise KeyError(jid)
            payload=json.loads(row[0]);payload.update(fields)
            db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),jid))

    def recover(self):
        with self.connect() as db:
            rows=db.execute('SELECT id,payload FROM jobs').fetchall()
            for jid,raw in rows:
                payload=json.loads(raw)
                if payload['status']=='processing':
                    payload.update(status='failed',error='service_restarted',stage='failed')
                    db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload),jid))

    def expired(self,before):
        with self.connect() as db:
            return [r[0] for r in db.execute('SELECT id FROM jobs WHERE created<?',(before,)) if self.get(r[0])['status']!='processing']

    def delete(self,jid):
        with self.connect() as db:db.execute('DELETE FROM jobs WHERE id=?',(jid,))
