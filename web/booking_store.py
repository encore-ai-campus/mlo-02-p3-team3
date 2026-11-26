"""MongoDB unit of work for booking workflow. Memory is a transaction-local view."""
import copy
import json
import logging
import sys
import time
import math
from collections.abc import MutableMapping
from contextlib import contextmanager
from threading import RLock, local
from pymongo.errors import PyMongoError
from .prevention_store import Store
from . import prevention
from .booking_schedule import transaction_time

logger = logging.getLogger('ticketguard.booking')


class Documents(MutableMapping):
    def __init__(self, collection, session, *, tuple_keys=False):
        self.collection, self.session = collection, session
        self.tuple_keys = tuple_keys
        self.cache, self.original, self.deleted = {}, {}, set()

    def key(self, key):
        return json.dumps(key, separators=(',', ':')) if self.tuple_keys else key

    def __getitem__(self, key):
        if key in self.deleted:
            raise KeyError(key)
        if key not in self.cache:
            row = self.collection.find_one({'_id': self.key(key)}, session=self.session)
            if not row:
                raise KeyError(key)
            self.cache[key] = row['value']
            self.original[key] = copy.deepcopy(row['value'])
        return self.cache[key]

    def __setitem__(self, key, value):
        if key not in self.original:
            try: self[key]
            except KeyError: self.original[key] = None
        self.deleted.discard(key)
        self.cache[key] = value

    def __delitem__(self, key):
        self[key]
        self.deleted.add(key)
        self.cache.pop(key, None)

    def __iter__(self):
        keys = {tuple(json.loads(row['_id'])) if self.tuple_keys else row['_id']
                for row in self.collection.find({}, {'_id': 1}, session=self.session)}
        return iter((keys | self.cache.keys()) - self.deleted)

    def __len__(self):
        return sum(1 for _ in self)

    def flush(self):
        for key in self.deleted:
            self.collection.delete_one({'_id': self.key(key)}, session=self.session)
        for key, value in self.cache.items():
            if value != self.original.get(key):
                self.collection.replace_one({'_id': self.key(key)}, {'_id': self.key(key), 'value': value},
                                            upsert=True, session=self.session)


class RepositoryLock:
    def __init__(self, repository):
        self.repository = repository
        self.contexts = local()

    @property
    def mutex(self):
        if not hasattr(self.contexts, 'mutex'): self.contexts.mutex = RLock()
        return self.contexts.mutex

    def __enter__(self):
        context = self.repository.unit_of_work()
        context.__enter__()
        if not hasattr(self.contexts, 'stack'):
            self.contexts.stack = []
        self.contexts.stack.append(context)
        return self

    def __exit__(self, *exception):
        return self.contexts.stack.pop().__exit__(*exception)


class MongoBookingMixin:
    persistent = True

    def __init__(self):
        # Connecting never resets server state. Imports also perform no DB I/O.
        self._local = local()
        self.lock = RepositoryLock(self)
        self.depth = 0

    @contextmanager
    def unit_of_work(self):
        with self.lock.mutex:
            if self.depth:
                self.depth += 1
                try: yield
                finally: self.depth -= 1
                return
            store = Store()
            try:
                store._prepare_booking()
                with store.transaction(guard=getattr(self._local, 'guard', 'ledger')) as session:
                    
                    token = prevention.active.set(session)
                    clock_token = transaction_time.set(time.time())
                    self.depth = 1
                    try:
                        self._load(session)
                        try:
                            yield
                        except Exception as exc:
                            if hasattr(exc, 'code') and hasattr(exc, 'status'):
                                self._flush(session)
                            raise
                        else:
                            self._flush(session)
                    finally:
                        self.depth = 0
                        prevention.active.reset(token)
                        transaction_time.reset(clock_token)
            except PyMongoError as exc:
                if exc.has_error_label('TransientTransactionError'): raise
                from .services import FlowError
                logger.warning('booking_storage_failed errorClass=%s code=%s', type(exc).__name__, getattr(exc, 'code', None))
                raise FlowError('예매 저장소를 확인할 수 없어요.', 'POLICY_UNAVAILABLE', 503) from None

    def execute(self, method, *args, **kwargs):
        if self.depth: return method(self, *args, **kwargs)
        name = method.__name__
        user = args[0] if args else kwargs.get('user')
        guard = 'ledger'
        if name == 'queue_status': guard = None
        elif name in ('sold_for','remaining_for'):
            guard='inventory:'+user
        elif name in ('current', 'opens_at', 'owned', 'challenge', 'verify', 'cancel', 'confirm', 'booking', 'history', 'scenario_context') and isinstance(user, str):
            guard = 'account:' + user
            if name == 'confirm':
                try:row = Store().db.booking_attempts.find_one({'_id':user}, {'value.show_id':1})
                except PyMongoError:
                    from .services import FlowError
                    raise FlowError('예매 저장소를 확인할 수 없어요.', 'POLICY_UNAVAILABLE', 503) from None
                if row: guard = [guard, 'inventory:' + row['value']['show_id']]
        deadline = time.monotonic() + 20
        while True:
            self._local.guard = guard
            try:
                with self.lock: return method(self, *args, **kwargs)
            except PyMongoError as exc:
                if not exc.has_error_label('TransientTransactionError') or time.monotonic() >= deadline:
                    from .services import FlowError
                    raise FlowError('예매 저장소를 확인할 수 없어요.', 'POLICY_UNAVAILABLE', 503) from None
                time.sleep(.01)
            finally:
                self._local.guard = 'ledger'

    def _load(self, session):
        self.attempts = Documents(session.db.booking_attempts, session.session)
        self.bookings = Documents(session.db.bookings, session.session)
        self.opens = Documents(session.db.booking_opens, session.session, tuple_keys=True)
        self.scenarios = Documents(session.db.booking_scenarios, session.session, tuple_keys=True)
        self.inventories = Documents(session.db.booking_inventory, session.session)
        state = session.db.booking_runtime.find_one({'_id': 'queue'}, session=session.session) or {}
        self.queue_counter = state.get('queue_counter', 0)
        self.admission_window = state.get('admission_window')
        self.admission_count = state.get('admission_count', 0)
        self.queue_before = self._queue()

    def _queue(self):
        return dict(queue_counter=self.queue_counter, admission_window=self.admission_window, admission_count=self.admission_count)

    def _flush(self, session):
        for mapping in (self.attempts, self.bookings, self.opens, self.scenarios, self.inventories):
            mapping.flush()
        if self._queue() != self.queue_before:
            session.db.booking_runtime.replace_one({'_id': 'queue'}, dict(_id='queue', **self._queue()),
                                                   upsert=True, session=session.session)

    def inventory(self, show_id):
        from .services import SHOWS, INITIAL_SOLD
        from .booking_schedule import window
        key = SHOWS[show_id]['schedule_id']
        sale = window(show_id)
        try:
            inventory = self.inventories[key]
        except KeyError:
            inventory = None
        if inventory is None or (sale and inventory.get('cycle') != sale['cycle']):
            self.inventories[key] = dict(show_id=show_id, sold=[] if sale else sorted(INITIAL_SOLD),
                                        remaining=28, cycle=sale['cycle'] if sale else None)
        return self.inventories[key]

    def sold_for(self, show_id):
        with self.lock:
            return set(self.inventory(show_id)['sold'])

    def remaining_for(self, show_id):
        with self.lock:
            return self.inventory(show_id)['remaining']

    def sell(self, show_id, seats, quantity):
        from .services import SHOWS
        inventory = self.inventory(show_id)
        if SHOWS[show_id]['kind'] == 'reserved':
            inventory['sold'] = sorted(set(inventory['sold']) | set(seats))
        else:
            inventory['remaining'] -= quantity

    def set_remaining(self, show_id, quantity):
        self.inventory(show_id)['remaining'] = quantity

    def _remember(self, rows):
        for row in rows:
            key=row['_id']
            if key not in self.attempts.cache:
                self.attempts.cache[key]=row['value']
                self.attempts.original[key]=copy.deepcopy(row['value'])

    def active_users(self):
        rows=list(self.attempts.collection.find({'value.stage': {'$in': ['queue','captcha','select']}}, session=self.attempts.session))
        self._remember(rows)
        users={r['_id'] for r in rows}
        users|={user for user,a in self.attempts.cache.items() if a['stage'] in ('queue','captcha','select')}
        return users-self.attempts.deleted

    def queue_status(self, a):
        from django.conf import settings
        if a['stage'] != 'queue': return 0,0
        now=time.time()
        excluded=list(self.attempts.cache.keys() | self.attempts.deleted)
        query={'value.stage':'queue','value.rank':{'$lte':a['rank']},
               '$or':[{'value.policy_restricted_until':{'$exists':False}}, {'value.policy_restricted_until':{'$lte':now}}]}
        if excluded:query['_id']={'$nin':excluded}
        rank=self.attempts.collection.count_documents(query, session=self.attempts.session)
        rank+=sum(row['stage']=='queue' and row.get('policy_restricted_until',0)<=now and row['rank']<=a['rank']
                  for key,row in self.attempts.cache.items() if key not in self.attempts.deleted)
        available=settings.BOOKING_ADMISSION_BATCH_SIZE-self.admission_count
        if rank<=available:return rank,0
        interval=settings.BOOKING_ADMISSION_INTERVAL_SECONDS
        wait=max(0,(self.admission_window or now)+interval-now)
        wait+=((rank-available-1)//settings.BOOKING_ADMISSION_BATCH_SIZE)*interval
        return rank,math.ceil(wait)

    def advance_queue(self):
        from django.conf import settings
        from .events import emit
        now=time.time();interval=settings.BOOKING_ADMISSION_INTERVAL_SECONDS
        if self.admission_window is None or now>=self.admission_window+interval:
            self.admission_window,self.admission_count=now,0
        # Expiry retains per-attempt evidence, but only expired rows are loaded.
        expired=list(self.attempts.collection.find({'value.stage':{'$in':['queue','captcha','select']},
            'value.heartbeat':{'$lt':now-settings.ATTEMPT_TIMEOUT_SECONDS}},session=self.attempts.session))
        self._remember(expired)
        for row in expired:self.current(row['_id'],False)
        for user in list(self.attempts.cache):self.current(user,False)
        available=max(0,settings.BOOKING_ADMISSION_BATCH_SIZE-self.admission_count)
        if not available:return
        query={'value.stage':'queue','$or':[{'value.policy_restricted_until':{'$exists':False}},
               {'value.policy_restricted_until':{'$lte':now}}]}
        excluded=list(self.attempts.cache.keys() | self.attempts.deleted)
        if excluded:query['_id']={'$nin':excluded}
        rows=list(self.attempts.collection.find(query,session=self.attempts.session).sort([('value.rank',1),('_id',1)]).limit(available))
        self._remember(rows)
        waiting=[(u,a) for u,a in self.attempts.cache.items() if u not in self.attempts.deleted and a['stage']=='queue' and a.get('policy_restricted_until',0)<=now]
        waiting.sort(key=lambda item:(item[1]['rank'],item[0]))
        for user,a in waiting[:available]:
            a['stage']='captcha';self.admission_count+=1
            emit(user,'queue_admitted',a,details={'elapsedMs':round((now-a['entered'])*1000,2)})

    def active_attempts(self):
        return (self.attempts[user] for user in self.active_users())

    def reset(self):
        from django.conf import settings
        if not settings.QA_RESET_ENABLED:
            raise ValueError('Persistent booking reset requires the explicit QA reset hook')
        with self.lock:
            session = prevention.active.get()
            for mapping in (self.attempts, self.bookings, self.opens, self.scenarios, self.inventories):
                for key in list(mapping): del mapping[key]
            self.queue_counter, self.admission_window, self.admission_count = 0, None, 0
            # Policy/purchase ledger is deliberately not reset by QA hooks.

    def history(self, user):
        with self.lock:
            session = prevention.active.get()
            return [row['value'] for row in session.db.bookings.find({'value.user': user}, session=session.session)
                    .sort([('value.created_at', -1), ('_id', -1)])]


def durable(method):
    from functools import wraps
    @wraps(method)
    def call(self, *args, **kwargs):
        return self.execute(method, *args, **kwargs)
    return call

# Each request/thread owns its unit-of-work view; the process-wide repository is stateless.
def _local_property(name, default=None):
    return property(lambda self: getattr(self._local,name,default), lambda self,value:setattr(self._local,name,value))
for _name in ('depth','attempts','bookings','opens','scenarios','inventories','queue_counter','admission_window','admission_count','queue_before'):
    setattr(MongoBookingMixin,_name,_local_property(_name,0 if _name=='depth' else None))
