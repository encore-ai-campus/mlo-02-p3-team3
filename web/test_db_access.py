"""Concurrency and recovery checks against the disposable real replica set."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from pymongo.errors import OperationFailure
from .accounts import create_account
from .events import build_event, publisher
from .outbox import Worker
from .prevention_store import Store
from .services import MongoRepository

@override_settings(EVENT_PUBLISHER='mock', CAPTURE_ENABLED=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DBAccessTests(SimpleTestCase):
    def setUp(self):
        publisher.records.clear();publisher.receipts.clear()

    def enqueue(self,n=1):
        with Store().transaction(guard=None) as session:
            for i in range(n):session.enqueue(build_event('worker-test','login_success'))

    def test_signup_does_not_wait_for_booking_guard_or_call_publisher(self):
        locked=threading.Event();release=threading.Event()
        def hold():
            with Store().transaction():
                locked.set();release.wait(5)
        with ThreadPoolExecutor(2) as pool:
            lock=pool.submit(hold);self.assertTrue(locked.wait(2))
            try:
                with patch.object(publisher,'publish',side_effect=AssertionError('request must not publish')):
                    pool.submit(create_account,'independent-user','isolated-secret').result(timeout=3)
            finally:release.set();lock.result()
        db=Store().db
        self.assertEqual(db.accounts.count_documents({}),1)
        self.assertEqual(db.outbox.count_documents({}),1)

    def test_readiness_detects_missing_index_without_repairing_it(self):
        db=Store().db
        self.assertEqual(Store().check_ready(),[])
        self.assertEqual(self.client.get('/ready/').status_code,200)
        db.booking_attempts.drop_index('value.stage_1_value.rank_1__id_1')
        self.assertTrue(Store().check_ready())
        self.assertEqual(self.client.get('/ready/').status_code,503)
        Store().prepare();self.assertEqual(Store().check_ready(),[])

    def test_two_workers_only_one_publishes_and_ack_is_required(self):
        self.enqueue();entered=threading.Event();ack=threading.Event()
        class Pending:
            def publish(self,event):entered.set();return {'state':'pending'}
            def status(self,*args):return {'state':'delivered' if ack.is_set() else 'pending'}
        one=Worker(Store(),delivery_seconds=3);two=Worker(Store());one.publisher=Pending()
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(one.drain)
            self.assertTrue(entered.wait(2))
            self.assertEqual(Store().db.outbox.count_documents({}),1)
            self.assertEqual(two.drain(),0)
            ack.set();self.assertEqual(first.result(timeout=3),1)
        self.assertEqual(Store().db.outbox.count_documents({}),0)

    def test_delivery_wait_longer_than_lease_renews_ownership(self):
        self.enqueue();clock=[time.time()]
        class Pending:
            calls=0
            def publish(self,event):return {'state':'pending'}
            def status(self,*args):
                self.calls+=1;clock[0]+=2
                return {'state':'delivered' if self.calls==3 else 'pending'}
        worker=Worker(Store(),lease_seconds=3,delivery_seconds=10);worker.publisher=Pending()
        with patch('web.outbox.time',wraps=time) as timer:
            timer.time.side_effect=lambda:clock[0];timer.monotonic.side_effect=lambda:clock[0]
            self.assertEqual(worker.drain(),1)

    def test_stop_during_pending_delivery_retains_event_and_releases_ownership(self):
        self.enqueue();stop=threading.Event();worker=Worker(Store(),stop=stop)
        class Pending:
            def publish(self,event):stop.set();return {'state':'pending'}
        worker.publisher=Pending()
        self.assertEqual(worker.drain(),0)
        row=Store().db.outbox.find_one()
        self.assertEqual(row['last_error'],'STOPPED')
        self.assertNotIn('worker',row)

    def test_expired_worker_lease_is_recovered_and_old_owner_cannot_delete(self):
        self.enqueue();store=Store();old=Worker(store);self.assertTrue(old.acquire())
        row=store.db.outbox.find_one()
        store.db.outbox.update_one({'_id':row['_id']},{'$set':{'worker':old.token,'lease_until':time.time()-1}})
        store.db.transaction_guard.update_one({'_id':'outbox_dispatch'},{'$set':{'lease_until':time.time()-1}})
        new=Worker(store);self.assertEqual(new.drain(),1)
        self.assertFalse(old.renew(row['_id']))

    def test_delivery_then_delete_failure_replays_same_identity(self):
        self.enqueue();store=Store();identity=store.db.outbox.find_one()['_id']
        from pymongo.synchronous.collection import Collection
        original=Collection.delete_one
        def fail(collection,*args,**kwargs):
            if collection.name=='outbox':raise OperationFailure('lost delete')
            return original(collection,*args,**kwargs)
        with patch.object(Collection,'delete_one',fail):self.assertEqual(Worker(store).drain(),0)
        self.assertEqual(store.db.outbox.find_one()['_id'],identity)
        store.db.outbox.update_one({'_id':identity},{'$set':{'retry_after':0}})
        self.assertEqual(Worker(store).drain(),1)
        self.assertEqual({e['eventId'] for e in publisher.records},{identity})

    def test_failed_head_is_retained_and_later_event_does_not_pass_it(self):
        self.enqueue(2);store=Store()
        with patch.object(publisher,'publish',side_effect=RuntimeError('offline')):
            self.assertEqual(Worker(store).drain(),0)
        head=store.db.outbox.find_one(sort=[('created',1),('_id',1)])
        self.assertGreater(head['retry_after'],time.time())
        self.assertEqual(Worker(store).drain(),0)
        self.assertEqual(store.db.outbox.count_documents({}),2)

    def test_queue_rank_includes_dirty_attempts_without_loading_every_user(self):
        db=Store().db;now=time.time()
        db.booking_runtime.insert_one(dict(_id='queue',queue_counter=200,admission_window=now,admission_count=40))
        db.booking_attempts.insert_many([dict(_id=f'user-{i}',value=dict(id=f'attempt-{i}',rank=i+1,
            stage='queue',heartbeat=now,policy_restricted_until=0)) for i in range(200)])
        repo=MongoRepository()
        with repo.lock:
            repo.attempts['user-0']['stage']='cancelled'
            self.assertEqual(repo.queue_status(repo.attempts['user-199'])[0],199)
            self.assertEqual(len(repo.attempts.cache),2)

    def test_repository_views_are_thread_local(self):
        repo=MongoRepository();barrier=threading.Barrier(2)
        def operation(user):
            # Separate account resources may execute simultaneously in one instance.
            repo._local.guard='account:'+user
            with repo.lock:
                repo.attempts[user]=dict(id=user,stage='cancelled')
                barrier.wait(timeout=3)
                self.assertEqual(set(repo.attempts.cache),{user})
        with ThreadPoolExecutor(2) as pool:list(pool.map(operation,['one','two']))

    def test_independent_schedules_can_confirm_in_same_repository_concurrently(self):
        repo=MongoRepository();attempts=[]
        for user,show in [('parallel-one','blue-hour'),('parallel-two','spring-concert')]:
            attempt=repo.start(user,show,'page-'+user)
            answer=repo.challenge(user,'page-'+user,attempt['id'])
            repo.verify(user,'page-'+user,attempt['id'],answer)
            attempts.append((user,attempt['id']))
        from .prevention_store import Session
        original=Session.purchase;barrier=threading.Barrier(2)
        seen=threading.local()
        def purchase(session,*args,**kwargs):
            if not getattr(seen,'entered',False):
                seen.entered=True
                barrier.wait(timeout=5)
            return original(session,*args,**kwargs)
        def confirm(pair):
            user,identity=pair
            return repo.confirm(user,'page-'+user,identity,1,['E1'],1)
        with patch.object(Session,'purchase',purchase),ThreadPoolExecutor(2) as pool:
            bookings=list(pool.map(confirm,attempts))
        self.assertEqual(len({b['id'] for b in bookings}),2)
        self.assertEqual(Store().db.bookings.count_documents({}),2)

    @override_settings(BOOKING_ADMISSION_BATCH_SIZE=1,BOOKING_ADMISSION_INTERVAL_SECONDS=600)
    def test_two_admitters_expire_stale_waiter_without_double_admission(self):
        repo=MongoRepository();attempts=[repo.start(f'queue-{i}','blue-hour',f'page-{i}') for i in range(3)]
        db=Store().db
        from django.conf import settings
        db.booking_attempts.update_one({'_id':'queue-1'},{'$set':{'value.heartbeat':time.time()-settings.ATTEMPT_TIMEOUT_SECONDS-1}})
        db.booking_runtime.update_one({'_id':'queue'},{'$set':{'admission_window':time.time()-601}})
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda _:MongoRepository().advance_queue(),range(2)))
        self.assertEqual(db.booking_attempts.find_one({'_id':'queue-1'})['value']['stage'],'expired')
        self.assertEqual(db.booking_attempts.find_one({'_id':'queue-2'})['value']['stage'],'captcha')
        self.assertEqual(db.outbox.count_documents({'payload.eventType':'queue_admitted','payload.userId':'queue-2'}),1)
