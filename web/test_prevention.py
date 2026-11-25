"""Isolated policy, lifetime quota, concurrency, restart and outbox regressions."""
import json
import tempfile
from pymongo.errors import OperationFailure
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings

from .events import publisher
from .login_guard import login_guard
from .prevention import Policy, login_attempt, scope
from .prevention_store import Store, Session
from .services import FlowError, MockRepository


@override_settings(EVENT_PUBLISHER='mock', CAPTURE_ENABLED=False)
class PreventionTests(SimpleTestCase):
    def setUp(self):
        root = Path(settings.BASE_DIR) / 'tmp'
        root.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)
        self.setting = override_settings(WEB_STATE_MONGO_URI=settings.WEB_STATE_MONGO_URI)
        self.setting.enable()
        self.addCleanup(self.setting.disable)
        login_guard.reset()
        publisher.receipts.clear()
        publisher.records.clear()
        self.target = scope('u', 'open-air', 'open-air-evening')

    def operation(self, at, function, target=None, attempt=None):
        with Store().transaction() as session:
            return function(Policy(session, target or self.target, attempt, clock=lambda: at))

    def events(self):
        with Store().transaction() as session:
            return [row['payload'] for row in session.db.outbox.find(session=session.session)]

    def test_account_login_without_window_and_success_reset(self):
        target = scope('account')
        for i in range(4):
            self.assertFalse(self.operation(100+i*10000, lambda p: p.count('login_repeated_failure'), target))
        self.assertTrue(self.operation(50000, lambda p: p.count('login_repeated_failure'), target))
        with self.assertRaises(FlowError):
            self.operation(50299, lambda p: p.check(), target)
        self.operation(50300, lambda p: p.check(), target)
        self.assertEqual(self.operation(50300, lambda p: p.state['counts']['login_repeated_failure'], target), 5)
        self.assertTrue(self.operation(50300, lambda p: p.count('login_repeated_failure'), target))
        with patch('web.prevention.time.time', return_value=50601):
            self.assertEqual(login_attempt('account', lambda: True), (True, False))
        self.assertEqual(self.operation(50601, lambda p: p.state['counts'], target), {})

    def test_account_lock_cannot_be_bypassed_with_a_new_browser(self):
        for _ in range(5):
            response = Client().post('/api/action/', json.dumps(dict(action='login', username='ticketguard', password='bad')), content_type='application/json')
        self.assertEqual(response.status_code, 429)
        blocked = Client().post('/api/action/', json.dumps(dict(action='login', username='ticketguard', password='ticketguard-demo')), content_type='application/json')
        self.assertEqual(blocked.status_code, 429)
        Store().drain()
        events = list(publisher.records)
        self.assertEqual(sum(e['eventType']=='prevention_detected' for e in events), 1)
        self.assertEqual(sum(e['eventType']=='prevention_applied' for e in events), 1)
        self.assertEqual(sum(e['eventType']=='prevention_rejected' for e in events), 1)
        self.assertNotIn('ticketguard-demo', json.dumps([e for e in events if e['eventType'].startswith('prevention_')]))

    def test_inclusive_windows_nine_ten_and_no_expiry_extension(self):
        for rule, seconds in [('not_open_repeat', 2), ('reentry_repeat', 2), ('selection_repeat', 5)]:
            target = scope(rule, 's', 'session')
            attempt = dict(id='a',page_id='p',show_id='s')
            for i in range(9):
                self.assertFalse(self.operation(100, lambda p: p.count(rule, identity=str(i)), target, attempt))
            self.assertTrue(self.operation(100+seconds, lambda p: p.count(rule, identity='last'), target, attempt))
            with self.assertRaises(FlowError):
                self.operation(100+seconds+200, lambda p: p.check(), target, attempt)
            self.assertEqual(self.operation(100+seconds+200, lambda p: p.state['locks'][rule]['until'], target, attempt), 400+seconds)
            self.operation(400+seconds, lambda p: p.check(), target, attempt)
        # An event just outside the boundary does not count.
        target=scope('outside', 's', 't')
        for i in range(9):
            self.operation(100, lambda p: p.count('not_open_repeat', identity=str(i)), target)
        self.assertFalse(self.operation(102.00001, lambda p: p.count('not_open_repeat'), target))

    def test_counter_scope_attempt_and_duplicate_identity_separation(self):
        attempt=lambda identity: dict(id=identity,page_id='p',show_id='open-air')
        for i in range(9):
            self.operation(100, lambda p: p.count('selection_repeat', identity=str(i)), attempt=attempt('a'))
        self.assertFalse(self.operation(100, lambda p: p.count('selection_repeat', identity='0'), attempt=attempt('a')))
        self.assertFalse(self.operation(100, lambda p: p.count('selection_repeat'), attempt=attempt('b')))
        for target in (scope('other','open-air','open-air-evening'), scope('u','other','other-evening'),
                       scope('u','open-air','other-session'), scope('u','open-air','open-air-evening', True)):
            self.assertFalse(self.operation(100, lambda p: p.count('selection_repeat'), target, attempt('a')))

    def test_opening_releases_only_preopen_restriction(self):
        def apply(p):
            p.detect('not_open_repeat', 10)
            p.detect('reentry_repeat', 10)
        self.operation(100, apply)
        self.operation(101, lambda p: p.release('not_open_repeat'))
        locks=self.operation(101, lambda p: p.state['locks'])
        self.assertEqual(set(locks), {'reentry_repeat'})

    def ready(self, repo, user='u', show='open-air'):
        a=repo.start(user, show, 'p')
        a['stage']='select'
        return a

    def test_preopen_start_only_tenth_and_open_release(self):
        repo=MockRepository()
        with patch('web.prevention.time.time', return_value=100):
            repo.opens['u','open-air']=101
            for _ in range(10):
                with self.assertRaises(FlowError) as error:
                    repo.start('u','open-air','p')
                self.assertEqual(error.exception.code,'NOT_OPEN')
            with self.assertRaises(FlowError) as error:
                repo.start('u','open-air','p')
            self.assertEqual(error.exception.code,'TEMPORARILY_RESTRICTED')
        with patch('web.prevention.time.time', return_value=101):
            self.assertEqual(repo.start('u','open-air','p')['stage'], 'captcha')

    def test_cancel_reentry_counts_expired_recovery_excluded(self):
        repo=MockRepository()
        with patch('web.prevention.time.time', return_value=100):
            for _ in range(9):
                a=repo.start('u','open-air','p')
                repo.cancel('u','p',a['id'])
            with self.assertRaises(FlowError) as error:
                repo.start('u','open-air','p')
            self.assertEqual(error.exception.code,'TEMPORARILY_RESTRICTED')
            restricted=repo.attempts['u']
            repo.advance_queue()
            self.assertEqual(restricted['stage'],'queue')
        repo=MockRepository()
        with patch('web.prevention.time.time', return_value=100):
            for _ in range(15):
                a=repo.start('recovery','open-air','p')
                a['heartbeat'] -= 1000
        Store().drain()
        events=list(publisher.records)
        excluded=[e for e in events if e['eventType']=='prevention_evaluated' and e['details'].get('entryCause')=='connection_expired']
        self.assertTrue(excluded)
        self.assertTrue(all(e['details']['excluded'] and e['details']['predecessorAttemptId'] for e in excluded))

    def test_selection_errors_and_inventory_exclusion(self):
        repo=MockRepository()
        a=self.ready(repo, show='blue-hour')
        with patch('web.prevention.time.time', return_value=100):
            for sequence in range(1, 13):
                with self.assertRaises(FlowError):
                    repo.confirm('u','p',a['id'],sequence,['A5'],1)
            for sequence, seats in enumerate([[],['E1','E1'],['Z99']]*3+[[]], 20):
                with self.assertRaises(FlowError):
                    repo.confirm('u','p',a['id'],sequence,seats,1)
        Store().drain()
        policy_events=[e for e in publisher.records if e['eventType']=='prevention_detected']
        self.assertEqual(len(policy_events),1)
        self.assertEqual(policy_events[0]['details']['ruleId'],'selection_repeat')

    def test_quota_restart_replay_and_confirmed_adjustments(self):
        repo=MockRepository()
        a=self.ready(repo)
        first=repo.confirm('u','p',a['id'],1,[],3)
        self.assertEqual(repo.confirm('u','p',a['id'],2,[],4)['id'],first['id'])
        repo=MockRepository()  # Purchase state must survive an actual new repository.
        remaining=repo.remaining
        self.assertEqual(repo.confirm('u','p',a['id'],99,[],4)['id'],first['id'])
        self.assertEqual(repo.remaining,remaining)
        a=self.ready(repo)
        second=repo.confirm('u','p',a['id'],1,[],1)
        a=self.ready(repo)
        with self.assertRaises(FlowError) as error:
            repo.confirm('u','p',a['id'],1,[],1)
        self.assertEqual(error.exception.code,'PURCHASE_LIMIT')
        with Store().transaction() as session:
            self.assertEqual(session.purchased(self.target),4)
            self.assertTrue(session.release('cancel-1',first['id'],1,'cancelled'))
            self.assertFalse(session.release('cancel-1',first['id'],1,'cancelled'))
            self.assertTrue(session.release('refund-1',first['id'],2,'refunded'))
            self.assertEqual(session.purchased(self.target),1)
            with self.assertRaises(ValueError):
                session.release('extra-refund',first['id'],1,'refunded')
            with self.assertRaises(ValueError):
                session.release('queue-cancel', 'nonexistent', 1, 'cancelled')
        self.assertNotEqual(first['id'],second['id'])

    def test_concurrent_quota_and_duplicate_attempt_are_atomic(self):
        def purchase(identity):
            with Store().transaction() as session:
                policy=Policy(session,self.target)
                existing=session.replay(identity)
                if existing:
                    return existing
                policy.check()
                policy.quota(3)
                return session.purchase(self.target,identity,dict(id='b-'+identity,quantity=3))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(purchase,['same','same']))
        self.assertEqual(results[0],results[1])
        with self.assertRaises(FlowError):
            purchase('different')
        with Store().transaction() as session:
            self.assertEqual(session.purchased(self.target),3)

    def test_different_concurrent_purchases_cannot_exceed_four(self):
        def purchase(identity):
            try:
                with Store().transaction() as session:
                    policy=Policy(session,self.target)
                    policy.check()
                    policy.quota(3)
                    session.purchase(self.target,identity,dict(id='booking-'+identity,quantity=3))
                    return True
            except FlowError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(purchase,['first','second']))
        self.assertEqual(sorted(results),[False,True])
        with Store().transaction() as session:
            self.assertEqual(session.purchased(self.target),3)

    def test_storage_failure_rolls_back_purchase_inventory_and_attempt(self):
        repo=MockRepository()
        a=self.ready(repo)
        original=Session.enqueue
        def enqueue(session,event):
            if event['eventType']=='booking_confirm_success':
                raise OperationFailure('isolated storage failure')
            return original(session,event)
        with patch.object(Session,'enqueue',enqueue):
            with self.assertRaises(FlowError) as error:
                repo.confirm('u','p',a['id'],1,[],2)
        self.assertEqual(error.exception.code,'POLICY_UNAVAILABLE')
        self.assertEqual(repo.remaining,28)
        self.assertEqual(repo.bookings,{})
        self.assertEqual(repo.attempts['u']['stage'],'select')
        self.assertEqual(repo.attempts['u']['sequence'],0)
        with Store().transaction() as session:
            self.assertEqual(session.purchased(self.target),0)

    def test_outbox_failure_retry_preserves_identity_and_transaction_rollback(self):
        self.operation(100,lambda p:p.detect('not_open_repeat',10))
        original=self.events()
        with patch.object(publisher,'publish',side_effect=RuntimeError('offline')):
            self.assertEqual(Store().drain(),0)
        self.assertEqual(self.events(),original)
        Store().db.outbox.update_many({}, {'$set': {'retry_after':0}})
        self.assertEqual(Store().drain(),2)
        self.assertEqual(self.events(),[])
        self.assertEqual({e['eventId'] for e in publisher.records},{e['eventId'] for e in original})
        with self.assertRaises(RuntimeError):
            with Store().transaction() as session:
                session.purchase(self.target,'rollback',dict(id='rollback',quantity=1))
                Policy(session,self.target,clock=lambda:100).detect('not_open_repeat',10)
                raise RuntimeError('partial failure')
        with Store().transaction() as session:
            self.assertEqual(session.purchased(self.target),0)
        self.assertEqual(self.events(),[])

    def test_actual_server_events_travel_through_verified_flow_to_admin_page(self):
        """Integration dependencies are available in the pipeline/admin test environment."""
        from importlib.util import find_spec
        if any(find_spec(name) is None for name in ('mongomock','early_monitor','ticket_guard_admin','fastapi')):
            self.skipTest('Requires pipeline/admin isolated integration test dependencies')
        import mongomock
        from early_monitor.events import project
        from configuration.user_flow import FlowConfig
        from user_flow.runtime import Processor
        from ticket_guard_admin.app import create_admin_dashboard_app
        from ticket_guard_admin.settings import AdminSettings
        from ticket_guard_admin.early_monitor.reader import read
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        now=2000000000
        with patch('web.prevention.time.time',return_value=now):
            for _ in range(5):
                login_attempt('login-integration',lambda:False)
            preopen=MockRepository()
            preopen.opens['preopen-integration','open-air']=now+100
            for _ in range(10):
                with self.assertRaises(FlowError):
                    preopen.start('preopen-integration','open-air','p')
            reentry=MockRepository()
            for i in range(10):
                if i==9:
                    with self.assertRaises(FlowError):
                        reentry.start('reentry-integration','open-air','p')
                else:
                    a=reentry.start('reentry-integration','open-air','p')
                    reentry.cancel('reentry-integration','p',a['id'])
            selection=MockRepository()
            a=self.ready(selection,user='selection-integration')
            for sequence in range(1,11):
                with self.assertRaises(FlowError):
                    selection.confirm('selection-integration','p',a['id'],sequence,[],0)
            quota=MockRepository()
            a=self.ready(quota,user='quota-integration')
            with self.assertRaises(FlowError):
                quota.confirm('quota-integration','p',a['id'],1,[],5)
        while Store().drain():
            pass
        values=[item for item in publisher.records if item['eventType'].startswith('prevention_')]
        self.assertEqual(len([item for item in values if item['eventType']=='prevention_detected']),5)
        database=mongomock.MongoClient('mongodb://localhost/target',tz_aware=True)
        config=FlowConfig('unused','events','isolated','unused','unused','isolated',self.directory.name)
        config.raw_wait_seconds=0
        records=[]
        for offset,value in enumerate(values):
            wire=json.dumps(value).encode()
            records.append(SimpleNamespace(topic='events',partition=0,offset=offset,value=wire))
            database.raw.unclassified_events.insert_one(dict(source='isolated',topic='events',partition=0,offset=offset,value=wire))
            self.assertIsNotNone(project(value))
        processor=Processor(config,database.raw,database.target,clock=lambda:now)
        processor.batch(SimpleNamespace(toLocalIterator=lambda **kwargs:iter(records)),0)
        with patch('ticket_guard_admin.early_monitor.reader.MongoClient',return_value=database):
            result=read('mongodb://unused/target',now=now)
        with patch('ticket_guard_admin.user_flow.pages.read_early',return_value=result):
            app=create_admin_dashboard_app(AdminSettings(mongo_uri='',base_path='/admin',_env_file=None))
            response=TestClient(app).get('/',headers={'X-Admin-User':'manager'})
        self.assertEqual(response.status_code,200)
        data=json.loads(response.text.split('<script type="application/json" data-sd-data>',1)[1].split('</script>',1)[0])
        metrics={metric['key']:metric for stage in data for metric in stage['metrics']}
        for rule in ('login_repeated_failure','not_open_repeat','reentry_repeat','selection_repeat','purchase_limit_attempt'):
            self.assertEqual(metrics[rule]['value'],1)
            self.assertEqual(metrics[rule]['action_count'],1)
            self.assertTrue(metrics[rule]['cases'][0]['evidence'])
