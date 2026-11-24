"""MongoDB-owned policy state, purchase ledger and transactional event outbox."""
import atexit
import copy
import time
from contextlib import contextmanager
from functools import lru_cache
from django.conf import settings
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


@lru_cache(maxsize=16)
def connection(uri):
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                         socketTimeoutMS=15000, appname='ticketguard-web-state')
    atexit.register(client.close)
    return client


class Store:
    def __init__(self):
        uri = settings.WEB_STATE_MONGO_URI
        if not uri:
            raise ValueError('WEB_STATE_MONGO_URI is required')
        self.client = connection(uri)
        self.db = self.client.get_default_database()

    def _prepare(self):
        # Collection/index creation is outside the data transaction.
        self.db.purchases.create_index('booking', unique=True)
        self.db.purchases.create_index('scope')
        self.db.outbox.create_index('created')
        self.db.browser_guards.create_index('expires_at', expireAfterSeconds=0)
        self.db.transaction_guard.update_one({'_id': 'ledger'}, {'$setOnInsert': {'revision': 0}}, upsert=True)

    def _prepare_booking(self):
        self.db.booking_attempts.create_index('value.id', unique=True)
        self.db.booking_attempts.create_index([('value.stage', 1), ('value.rank', 1)])
        self.db.bookings.create_index([('value.user', 1), ('value.created_at', -1)])

    @contextmanager
    def transaction(self):
        self._prepare()
        # The first write serializes transactions across processes, replacing the
        # old database-wide writer serialization. Retry BEFORE yielding only:
        # callers can mutate in-memory inventory and must never be replayed.
        with self.client.start_session() as mongo_session:
            deadline = time.monotonic() + 20
            while True:
                mongo_session.start_transaction(read_concern=ReadConcern('snapshot'),
                                                write_concern=WriteConcern('majority'))
                try:
                    self.db.transaction_guard.update_one({'_id': 'ledger'}, {'$inc': {'revision': 1}}, session=mongo_session)
                    break
                except PyMongoError as exc:
                    mongo_session.abort_transaction()
                    if not exc.has_error_label('TransientTransactionError') or time.monotonic() >= deadline:
                        raise
                    time.sleep(.02)
            try:
                yield Session(self.db, mongo_session)
            except Exception as exc:
                # Business rejections still persist counters, locks and evidence.
                if hasattr(exc, 'code') and hasattr(exc, 'status'):
                    self._commit(mongo_session)
                else:
                    mongo_session.abort_transaction()
                raise
            else:
                self._commit(mongo_session)

    @staticmethod
    def _commit(session):
        deadline = time.monotonic() + 20
        while True:
            try:
                session.commit_transaction()
                return
            except PyMongoError as exc:
                if not exc.has_error_label('UnknownTransactionCommitResult') or time.monotonic() >= deadline:
                    raise

    def drain(self, limit=100):
        from .events import publisher
        delivered = []
        for row in self.db.outbox.find().sort([('created', 1), ('_id', 1)]).limit(limit):
            if publisher.publish(row['payload'])['state'] == 'delivered':
                delivered.append(row['_id'])
        if delivered:
            self.db.outbox.delete_many({'_id': {'$in': delivered}})
        return len(delivered)


class Session:
    def __init__(self, db, session):
        self.db, self.session = db, session

    def get(self, key, default=None):
        row = self.db.state.find_one({'_id': key}, session=self.session)
        return row['value'] if row else copy.deepcopy(default)

    def put(self, key, value):
        self.db.state.replace_one({'_id': key}, {'_id': key, 'value': value}, upsert=True, session=self.session)

    def enqueue(self, event):
        self.db.outbox.insert_one({'_id': event['eventId'], 'payload': event, 'created': time.time()}, session=self.session)

    def purchased(self, scope):
        return sum(row['quantity'] - row['released'] for row in self.db.purchases.find({'scope': scope}, session=self.session))

    def replay(self, attempt):
        row = self.db.purchases.find_one({'_id': attempt}, session=self.session)
        return row['payload'] if row else None

    def purchase(self, scope, attempt, booking):
        existing = self.replay(attempt)
        if existing:
            return existing
        if self.purchased(scope) + booking['quantity'] > 4:
            raise ValueError('Purchase must be checked inside the same transaction')
        self.db.purchases.insert_one(dict(_id=attempt, scope=scope, booking=booking['id'],
            quantity=booking['quantity'], released=0, payload=booking), session=self.session)
        return booking

    def release(self, identity, booking, quantity, kind):
        if not identity or kind not in ('cancelled', 'refunded') or type(quantity) is not int or quantity < 1:
            raise ValueError('Invalid confirmed adjustment')
        previous = self.db.adjustments.find_one({'_id': identity}, session=self.session)
        if previous:
            if (previous['booking'], previous['quantity'], previous['kind']) != (booking, quantity, kind):
                raise ValueError('Conflicting adjustment ID')
            return False
        row = self.db.purchases.find_one({'booking': booking}, session=self.session)
        if not row or row['released'] + quantity > row['quantity']:
            raise ValueError('Unknown booking or excessive adjustment')
        self.db.adjustments.insert_one(dict(_id=identity, booking=booking, quantity=quantity, kind=kind), session=self.session)
        self.db.purchases.update_one({'_id': row['_id']}, {'$inc': {'released': quantity}}, session=self.session)
        return True
