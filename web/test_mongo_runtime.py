"""Actual MongoDB workflow, cross-process admission, guard and session regressions."""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.test import Client, SimpleTestCase, override_settings
from django.utils import timezone
from pymongo.errors import OperationFailure
from pymongo.synchronous.collection import Collection
from .services import MongoRepository, FlowError
from .prevention_store import Store
from .login_guard import LoginGuard
from .mongo_sessions import SessionStore


@override_settings(EVENT_PUBLISHER='mock', CAPTURE_ENABLED=False, QA_CAPTCHA_FIXED_ANSWER='QA123')
class MongoRuntimeTests(SimpleTestCase):
    def ready(self, user, show='blue-hour'):
        repo=MongoRepository()
        attempt=repo.start(user,show,'page-'+user)
        answer=repo.challenge(user,'page-'+user,attempt['id'])
        self.assertEqual(answer,'QA123')
        repo.verify(user,'page-'+user,attempt['id'],answer)
        return attempt

    def worker(self, program, **values):
        env=dict(os.environ,WEB_STATE_MONGO_URI=settings.WEB_STATE_MONGO_URI,
                 EVENT_PUBLISHER='mock',CAPTURE_ENABLED='0',**values)
        result=subprocess.run([sys.executable,'-c',"import os,django;os.environ['DJANGO_SETTINGS_MODULE']='config.settings';django.setup();"+program],
                              env=env,capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_booking_captcha_history_and_inventory_survive_new_process(self):
        attempt=self.ready('restart')
        repo=MongoRepository()
        with repo.lock:
            persisted=repo.attempts['restart']
            self.assertEqual(persisted['stage'],'select')
            self.assertNotEqual(persisted['captcha'],'QA123')
        booking=repo.confirm('restart','page-restart',attempt['id'],1,['E1'],1)
        result=self.worker("from web.services import repo;import json;print(json.dumps(dict(history=repo.history('restart'),sold=sorted(repo.sold_for('blue-hour')))))")
        self.assertEqual(result['history'][0]['id'],booking['id'])
        self.assertIn('E1',result['sold'])
        self.assertEqual(MongoRepository().confirm('restart','page-restart',attempt['id'],2,['E2'],1)['id'],booking['id'])

    def test_cross_process_queue_admission_and_rank_are_global(self):
        program="from web.services import repo;import json;print(json.dumps(repo.start(os.environ['USER_ID'],'blue-hour','page')))"
        def start(user):
            return self.worker(program,USER_ID=user,BOOKING_ADMISSION_BATCH_SIZE='1',BOOKING_ADMISSION_INTERVAL_SECONDS='600')
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts=list(pool.map(start,['queue-one','queue-two']))
        self.assertEqual(sorted(a['rank'] for a in attempts),[1,2])
        self.assertEqual(sorted(a['stage'] for a in attempts),['captcha','queue'])
        repo=MongoRepository()
        waiting=next(a for a in attempts if a['stage']=='queue')
        # The timestamp is a UTC epoch shared by processes, not a process clock.
        self.assertGreater(waiting['heartbeat'],1_700_000_000)
        with repo.lock:
            repo.admission_window-=601
        repo.advance_queue()
        with repo.lock:
            self.assertEqual(sorted(a['stage'] for a in repo.active_attempts()),['captcha','captcha'])

    def test_two_repositories_cannot_sell_same_seat(self):
        attempts=[self.ready('seat-one'),self.ready('seat-two')]
        def confirm(pair):
            user,a=pair
            try:return MongoRepository().confirm(user,'page-'+user,a['id'],1,['E1'],1)['id']
            except FlowError as error:
                self.assertEqual(error.code,'SEAT_SOLD')
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(confirm,zip(['seat-one','seat-two'],attempts)))
        self.assertEqual(sum(result is not None for result in results),1)
        self.assertEqual(Store().db.bookings.count_documents({}),1)

    def test_general_stock_cannot_be_oversold(self):
        repo=MongoRepository()
        with repo.lock: repo.set_remaining('open-air',2)
        attempts=[self.ready('stock-one','open-air'),self.ready('stock-two','open-air')]
        def confirm(pair):
            user,a=pair
            try:return MongoRepository().confirm(user,'page-'+user,a['id'],1,[],2)['id']
            except FlowError as error:
                self.assertEqual(error.code,'INSUFFICIENT_STOCK')
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(confirm,zip(['stock-one','stock-two'],attempts)))
        self.assertEqual(sum(result is not None for result in results),1)
        self.assertEqual(repo.remaining_for('open-air'),0)

    def test_seat_inventory_is_independent_per_schedule(self):
        one=self.ready('first','blue-hour');two=self.ready('second','spring-concert')
        repo=MongoRepository()
        repo.confirm('first','page-first',one['id'],1,['E1'],1)
        self.assertNotIn('E1',repo.sold_for('spring-concert'))
        repo.confirm('second','page-second',two['id'],1,['E1'],1)
        self.assertEqual(Store().db.bookings.count_documents({}),2)

    def test_failed_inventory_write_aborts_booking_attempt_and_outbox(self):
        a=self.ready('rollback')
        original=Collection.replace_one
        def fail(collection,*args,**kwargs):
            if collection.name=='booking_inventory':raise OperationFailure('injected storage failure')
            return original(collection,*args,**kwargs)
        with patch.object(Collection,'replace_one',fail):
            with self.assertRaises(FlowError) as caught:
                MongoRepository().confirm('rollback','page-rollback',a['id'],1,['E1'],1)
        self.assertEqual(caught.exception.status,503)
        repo=MongoRepository()
        self.assertEqual(repo.current('rollback')['stage'],'select')
        self.assertNotIn('E1',repo.sold_for('blue-hour'))
        self.assertEqual(repo.history('rollback'),[])
        self.assertEqual(Store().db.purchases.count_documents({}),0)
        self.assertEqual(Store().db.outbox.count_documents({'payload.eventType':'booking_confirm_success'}),0)

    def test_expired_attempt_is_persistently_rejected(self):
        repo=MongoRepository();a=repo.start('expired','blue-hour','page')
        with repo.lock: repo.attempts['expired']['heartbeat']=time.time()-settings.ATTEMPT_TIMEOUT_SECONDS-1
        with self.assertRaises(FlowError) as caught: MongoRepository().current('expired')
        self.assertEqual(caught.exception.code,'EXPIRED')
        with repo.lock: self.assertEqual(repo.attempts['expired']['stage'],'expired')

    def test_browser_lock_survives_restart_and_success_clears(self):
        for expected in range(1,6):
            self.assertEqual(LoginGuard().fail('browser')[0],expected)
        self.assertTrue(LoginGuard().is_locked('browser'))
        db=Store().db
        self.assertIsNone(db.browser_guards.find_one({'_id':'browser'}))
        db.browser_guards.update_one({'_id':LoginGuard.identity('browser')},{'$set':{'locked_until':time.time()-1}})
        self.assertFalse(LoginGuard().is_locked('browser'))
        self.assertEqual(LoginGuard().fail('browser'),(1,False))
        LoginGuard().succeed('browser')
        self.assertEqual(LoginGuard().entries,{})

    def test_browser_failure_counts_are_atomic_across_processes(self):
        program="from web.login_guard import LoginGuard;import json;print(json.dumps(LoginGuard().fail('shared-browser')))"
        with ThreadPoolExecutor(max_workers=3) as pool:
            values=list(pool.map(lambda _:self.worker(program),range(5)))
        self.assertEqual(sorted(row[0] for row in values),[1,2,3,4,5])
        self.assertTrue(LoginGuard().is_locked('shared-browser'))

    def test_server_session_cookie_contains_only_opaque_id(self):
        client=Client()
        response=client.post('/api/action/',json.dumps(dict(action='login',username='ticketguard',password='ticketguard-demo')),content_type='application/json')
        self.assertEqual(response.status_code,200)
        key=client.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertEqual(len(key),32)
        self.assertNotIn('ticketguard',key)
        row=Store().db.web_sessions.find_one({'_id':SessionStore.identity(key)})
        self.assertIsNotNone(row)
        self.assertNotIn(key,json.dumps(row,default=str))
        self.assertNotIn('ticketguard-demo',row['data'])
        result=self.worker("from web.mongo_sessions import SessionStore;import json;print(json.dumps(SessionStore(os.environ['SESSION_KEY']).load()))",SESSION_KEY=key)
        self.assertEqual(result['user'],'ticketguard')
        self.assertEqual(client.post('/api/action/',json.dumps(dict(action='logout')),content_type='application/json').status_code,200)
        self.assertEqual(SessionStore(key).load(),{})

    def test_expired_session_and_concurrent_logout_cannot_resurrect(self):
        session=SessionStore();session['user']='user';session.save();key=session.session_key
        stale=SessionStore(key);self.assertEqual(stale['user'],'user')
        SessionStore(key).delete()
        stale['role']='admin'
        with self.assertRaises(UpdateError):stale.save()
        self.assertEqual(SessionStore(key).load(),{})
        session=SessionStore();session['user']='expired';session.save();key=session.session_key
        Store().db.web_sessions.update_one({'_id':SessionStore.identity(key)},{'$set':{'expires_at':timezone.now()-timedelta(seconds=1)}})
        self.assertEqual(SessionStore(key).load(),{})

    def test_initializing_repository_does_not_reset_state(self):
        repo=MongoRepository();a=repo.start('persistent','blue-hour','page')
        self.assertEqual(MongoRepository().current('persistent')['id'],a['id'])
        with override_settings(QA_RESET_ENABLED=False):
            with self.assertRaises(ValueError):MongoRepository().reset()
        self.assertEqual(repo.current('persistent')['id'],a['id'])

    def test_http_storage_failure_is_503_and_never_authenticates(self):
        from pymongo import MongoClient
        client=Client()
        session=client.session;session['user']='outage';session.save()
        client.cookies[settings.SESSION_COOKIE_NAME]=session.session_key
        unreachable=MongoClient('mongodb://127.0.0.1:1/unavailable',serverSelectionTimeoutMS=100,connectTimeoutMS=100)
        self.addCleanup(unreachable.close)
        with patch('web.prevention_store.connection',return_value=unreachable):
            response=client.get('/history/')
        self.assertEqual(response.status_code,503)
        self.assertNotIn(settings.SESSION_COOKIE_NAME,response.cookies)

    def test_captcha_hash_can_be_verified_from_another_process(self):
        repo=MongoRepository();a=repo.start('challenge','blue-hour','page')
        answer=repo.challenge('challenge','page',a['id'])
        with repo.lock:
            self.assertNotEqual(repo.attempts['challenge']['captcha'],answer)
        result=self.worker("from web.services import repo;import json;print(json.dumps(repo.verify('challenge','page',os.environ['ATTEMPT_ID'],'QA123')))",ATTEMPT_ID=a['id'])
        self.assertEqual(result['stage'],'select')

    def test_qa_reset_clears_booking_state_but_not_purchase_ledger(self):
        a=self.ready('qa-reset')
        repo=MongoRepository();booking=repo.confirm('qa-reset','page-qa-reset',a['id'],1,['E1'],1)
        with override_settings(QA_RESET_ENABLED=True):repo.reset()
        self.assertEqual(MongoRepository().history('qa-reset'),[])
        self.assertIsNotNone(Store().db.purchases.find_one({'booking':booking['id']}))

    def test_workflow_event_failure_aborts_attempt_and_queue(self):
        from .prevention_store import Session
        original=Session.enqueue
        def fail(session,event):
            if event['eventType']=='queue_enter':raise OperationFailure('injected outbox failure')
            return original(session,event)
        with patch.object(Session,'enqueue',fail):
            with self.assertRaises(FlowError) as caught:MongoRepository().start('outbox-rollback','blue-hour','page')
        self.assertEqual(caught.exception.status,503)
        self.assertIsNone(MongoRepository().current('outbox-rollback',False))
        self.assertEqual(Store().db.outbox.count_documents({}),0)
        from .events import publisher
        self.assertFalse(any(e['userId']=='outbox-rollback' for e in publisher.records))

    def test_http_booking_and_history_use_production_repository(self):
        client=Client()
        def action(command,**values):
            return client.post('/api/action/',json.dumps(dict(action=command,**values)),content_type='application/json')
        self.assertEqual(action('login',username='ticketguard',password='ticketguard-demo').status_code,200)
        started=action('start',showId='blue-hour',pageId='production-page')
        self.assertEqual(started.status_code,200)
        identity=started.json()['id']
        image=client.get('/captcha.png',dict(pageId='production-page',attemptId=identity))
        self.assertEqual(image.status_code,200)
        verified=action('verify',pageId='production-page',attemptId=identity,answer='QA123')
        self.assertEqual(verified.status_code,200)
        confirmed=action('confirm',pageId='production-page',attemptId=identity,sequence=1,seats=['E1'],quantity=1)
        self.assertEqual(confirmed.status_code,200)
        booking=confirmed.json()['redirect'].strip('/').split('/')[-1]
        other=Client();other.cookies[settings.SESSION_COOKIE_NAME]=client.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertContains(other.get('/history/'),booking)
        self.assertEqual(MongoRepository().history('ticketguard')[0]['id'],booking)
