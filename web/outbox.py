"""Ordered at-least-once dispatcher with renewable, fenced ownership.

One active dispatcher preserves the existing global created/_id ordering.
Additional workers are hot standbys, not unordered parallel publishers.
"""
import logging
import threading
import time
from uuid import uuid4
from pymongo import ReturnDocument
from django.conf import settings

log=logging.getLogger('ticketguard.outbox')

class Worker:
    def __init__(self, store, *, lease_seconds=30, delivery_seconds=None, stop=None):
        from .events import publisher
        self.store,self.publisher=store,publisher
        self.lease_seconds=max(3,lease_seconds)
        self.delivery_seconds=delivery_seconds if delivery_seconds is not None else settings.KAFKA_DELIVERY_TIMEOUT_MS/1000+2
        self.stop=stop or threading.Event()
        self.token=uuid4().hex

    def acquire(self):
        now=time.time()
        return self.store.db.transaction_guard.find_one_and_update(
            {'_id':'outbox_dispatch', '$or':[{'lease_until':{'$lte':now}}, {'owner':self.token}]},
            {'$set':{'owner':self.token,'lease_until':now+self.lease_seconds}},return_document=ReturnDocument.AFTER)

    def renew(self, identity):
        now=time.time();until=now+self.lease_seconds
        owned=self.store.db.transaction_guard.update_one(
            {'_id':'outbox_dispatch','owner':self.token,'lease_until':{'$gt':now}},
            {'$set':{'lease_until':until}}).matched_count
        if not owned:return False
        return bool(self.store.db.outbox.update_one({'_id':identity,'worker':self.token},
            {'$set':{'lease_until':until}}).matched_count)

    def release(self):
        self.store.db.outbox.update_many({'worker':self.token}, {'$unset':{'worker':'','lease_until':''}})
        self.store.db.transaction_guard.update_one({'_id':'outbox_dispatch','owner':self.token},
            {'$set':{'lease_until':0},'$unset':{'owner':''}})

    def drain(self, limit=100):
        delivered=0
        if not self.acquire():return 0
        try:
            for _ in range(limit):
                if self.stop.is_set():break
                row=self.store.db.outbox.find_one(sort=[('created',1),('_id',1)])
                if not row or row.get('retry_after',0)>time.time():break
                if not self.acquire():break
                row=self.store.db.outbox.find_one_and_update(
                    {'_id':row['_id'],'$or':[{'lease_until':{'$exists':False}},
                         {'lease_until':{'$lte':time.time()}},{'worker':self.token}]},
                    {'$set':{'worker':self.token,'lease_until':time.time()+self.lease_seconds},
                     '$inc':{'dispatch_attempts':1}},return_document=ReturnDocument.AFTER)
                if not row:break
                identity=row['_id'];event=row['payload']
                try:
                    status=self.publisher.publish(event)
                    deadline=time.monotonic()+self.delivery_seconds
                    next_renew=time.monotonic()+self.lease_seconds/3
                    while status['state'] not in ('delivered','failed') and not self.stop.is_set() and time.monotonic()<deadline:
                        if time.monotonic()>=next_renew:
                            if not self.renew(identity):return delivered
                            next_renew=time.monotonic()+self.lease_seconds/3
                        self.stop.wait(.05)
                        status=self.publisher.status(identity,event.get('userId')) or status
                    if not self.renew(identity):return delivered
                    if status['state']=='delivered':
                        delivered+=self.store.db.outbox.delete_one({'_id':identity,'worker':self.token}).deleted_count
                        continue
                    error=status.get('errorCode') or ('STOPPED' if self.stop.is_set() else 'DELIVERY_PENDING')
                except Exception as exc:
                    error=type(exc).__name__
                    log.warning('outbox_dispatch_failed errorClass=%s',error)
                delay=min(60,2**min(row['dispatch_attempts'],6))
                self.store.db.outbox.update_one({'_id':identity,'worker':self.token},
                    {'$set':{'retry_after':time.time()+delay,'last_error':error}})
                log.warning('outbox_head_blocked attempts=%d retrySeconds=%d errorClass=%s',row['dispatch_attempts'],delay,error)
                # Never pass a failed head record: preserve order and expose backlog.
                break
        finally:self.release()
        return delivered
