"""Web-owned durable policy state, purchase ledger and transactional event outbox.

One local SQLite file on durable storage; never the QA database or Django sessions.
BEGIN IMMEDIATE serializes account quotas across workers sharing this file.
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings


class Store:
    def __init__(self, path=None):
        self.path = str(path or settings.TICKETING_STATE_PATH)
        if self.path == ':memory:' or not self.path or self.path.startswith('file:'):
            raise ValueError('A durable web state file path is required')

    @contextmanager
    def transaction(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS purchases (attempt TEXT PRIMARY KEY, scope TEXT NOT NULL, booking TEXT NOT NULL UNIQUE, quantity INTEGER NOT NULL, released INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS purchases_scope ON purchases(scope)')
            db.execute('CREATE TABLE IF NOT EXISTS adjustments (id TEXT PRIMARY KEY, booking TEXT NOT NULL, quantity INTEGER NOT NULL, kind TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            yield Session(db)
        except Exception as exc:
            # Rejections are successful policy transactions: persist counters/locks.
            if hasattr(exc, 'code') and hasattr(exc, 'status'):
                db.commit()
            else:
                db.rollback()
            raise
        else:
            db.commit()
        finally:
            db.close()

    def drain(self, limit=100):
        """Retry same immutable IDs. Pending/failed Kafka sends stay durable.

        No transaction is held while sending. A crash before acknowledgement causes
        a safe redelivery, not deletion of evidence. Invoke periodically via command.
        """
        from .events import publisher
        with self.transaction() as session:
            events = session.db.execute('SELECT id,payload FROM outbox ORDER BY rowid LIMIT ?', (limit,)).fetchall()
        delivered = []
        for identity, payload in events:
            result = publisher.publish(json.loads(payload))
            if result['state'] == 'delivered':
                delivered.append((identity,))
        if not delivered:
            return 0
        with self.transaction() as session:
            session.db.executemany('DELETE FROM outbox WHERE id=?', delivered)
        return len(delivered)


class Session:
    def __init__(self, db):
        self.db = db

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (key, json.dumps(value, sort_keys=True)))

    def enqueue(self, event):
        self.db.execute('INSERT INTO outbox VALUES (?,?)', (event['eventId'], json.dumps(event, sort_keys=True)))

    def purchased(self, scope):
        return self.db.execute('SELECT COALESCE(SUM(quantity-released),0) FROM purchases WHERE scope=?', (scope,)).fetchone()[0]

    def replay(self, attempt):
        row = self.db.execute('SELECT payload FROM purchases WHERE attempt=?', (attempt,)).fetchone()
        return json.loads(row[0]) if row else None

    def purchase(self, scope, attempt, booking):
        existing = self.replay(attempt)
        if existing:
            return existing
        if self.purchased(scope) + booking['quantity'] > 4:
            raise ValueError('Purchase must be checked inside the same transaction')
        self.db.execute('INSERT INTO purchases(attempt,scope,booking,quantity,payload) VALUES (?,?,?,?,?)',
                        (attempt, scope, booking['id'], booking['quantity'], json.dumps(booking)))
        return booking

    def release(self, identity, booking, quantity, kind):
        """Trusted confirmed cancellation/refund adapter, no public endpoint.

        quantity is a newly released delta; a stable source adjustment ID is required.
        Refunding an already cancelled quantity must not release it a second time.
        """
        if not identity or kind not in ('cancelled', 'refunded') or type(quantity) is not int or quantity < 1:
            raise ValueError('Invalid confirmed adjustment')
        previous = self.db.execute('SELECT booking,quantity,kind FROM adjustments WHERE id=?', (identity,)).fetchone()
        if previous:
            if previous != (booking, quantity, kind):
                raise ValueError('Conflicting adjustment ID')
            return False
        row = self.db.execute('SELECT quantity,released FROM purchases WHERE booking=?', (booking,)).fetchone()
        if not row or row[1] + quantity > row[0]:
            raise ValueError('Unknown booking or excessive adjustment')
        self.db.execute('INSERT INTO adjustments VALUES (?,?,?,?)', (identity, booking, quantity, kind))
        self.db.execute('UPDATE purchases SET released=released+? WHERE booking=?', (quantity, booking))
        return True
