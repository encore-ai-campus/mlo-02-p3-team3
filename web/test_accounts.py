"""실제 MongoDB 계정 생성·중복·해시·이벤트·로그인 권한 검사."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.test import Client, SimpleTestCase, override_settings
from pymongo.errors import AutoReconnect

from .accounts import AccountError, create_account
from .prevention_store import Store


@override_settings(EVENT_PUBLISHER='mock', CAPTURE_ENABLED=False)
class AccountTests(SimpleTestCase):
    password = 'Signup-test-secret-456'

    def action(self, action, **fields):
        return self.client.post('/api/action/', json.dumps({'action': action, **fields}), content_type='application/json')

    def signup(self, username='new-customer', **fields):
        return self.action('signup', username=username, password=self.password, **fields)

    def test_signup_persists_hash_and_success_event_then_logs_in(self):
        response = self.signup('New-Customer', role='admin')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['redirect'], '/login/?registered=1')
        db = Store().db
        account = db.accounts.find_one({'_id': 'new-customer'})
        self.assertEqual(account['role'], 'customer')
        self.assertNotEqual(account['password_hash'], self.password)
        self.assertTrue(check_password(self.password, account['password_hash']))
        self.assertNotIn('password', account)
        self.assertEqual([event['eventType'] for event in db.account_events.find().sort('occurredAt', 1)],
                         ['signup_requested', 'signup_success'])
        self.assertNotIn(self.password, str(list(db.account_events.find())))
        self.assertNotIn('user', self.client.session)
        self.assertEqual(self.action('login', username='NEW-CUSTOMER', password=self.password).status_code, 200)
        self.assertEqual(self.client.session['user'], 'new-customer')
        self.assertEqual(self.client.session['role'], 'customer')
        self.assertEqual(self.client.get('/auth/check-admin/').status_code, 401)

    def test_duplicate_cannot_overwrite_account_or_password(self):
        self.assertEqual(self.signup().status_code, 201)
        before = Store().db.accounts.find_one({'_id': 'new-customer'})
        response = self.action('signup', username='NEW-CUSTOMER', password='Different-secret-789')
        self.assertEqual((response.status_code, response.json()['code']), (409, 'USERNAME_TAKEN'))
        self.assertEqual(Store().db.accounts.find_one({'_id': 'new-customer'}), before)
        self.assertEqual(Store().db.account_events.count_documents({'eventType': 'signup_success'}), 1)
        self.assertEqual(Store().db.account_events.find_one({'eventType': 'signup_fail'})['errorCode'], 'USERNAME_TAKEN')

    def test_invalid_input_is_rejected_and_failure_recorded(self):
        for username,password in ((None,self.password), ({'$gt':''},self.password),
                                  ('ab',self.password),('with space',self.password),('a'*33,self.password),
                                  ('valid-user','short'),('valid-user','x'*129),('valid-user',None)):
            with self.subTest(username=username, password_type=type(password).__name__):
                response = self.action('signup', username=username, password=password)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Store().db.accounts.count_documents({}), 0)
        self.assertEqual(Store().db.account_events.count_documents({'eventType': 'signup_fail'}), 8)

    def test_development_admin_and_qa_names_cannot_be_registered(self):
        for username in (settings.DEMO_USERNAME, settings.DEMO_SECOND_USERNAME, 'qa-user-0001', 'qa-user-custom'):
            with self.subTest(username=username):
                self.assertEqual(self.signup(username).json()['code'], 'USERNAME_RESERVED')
        with override_settings(DEMO_ADMIN_USERNAME='console-admin'):
            self.assertEqual(self.signup('CONSOLE-ADMIN').json()['code'], 'USERNAME_RESERVED')
        self.assertEqual(Store().db.accounts.count_documents({}), 0)

    def test_password_check_and_logout_for_registered_account(self):
        self.signup()
        self.assertEqual(self.action('login', username='new-customer', password='wrong-password').status_code, 401)
        self.assertEqual(self.action('login', username='new-customer', password=self.password).status_code, 200)
        self.assertEqual(self.action('logout').status_code, 200)
        self.assertNotIn('user', self.client.session)

    def test_concurrent_signup_only_creates_one_account(self):
        def register():
            try:
                create_account('concurrent-user', self.password)
                return 'created'
            except AccountError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: register(), range(2)))
        self.assertCountEqual(results, ['created', 'USERNAME_TAKEN'])
        self.assertEqual(Store().db.accounts.count_documents({}), 1)
        self.assertEqual(Store().db.account_events.count_documents({'eventType': 'signup_success'}), 1)

    def test_signup_csrf_is_enforced(self):
        response = Client(enforce_csrf_checks=True).post('/api/action/',
            json.dumps({'action': 'signup', 'username': 'csrf-user', 'password': self.password}),
            content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Store().db.accounts.count_documents({}), 0)

    def test_storage_failure_does_not_report_signup_success(self):
        with patch('web.accounts.Store', side_effect=AutoReconnect('storage unavailable')):
            response = self.signup()
        self.assertEqual((response.status_code, response.json()['code']), (503, 'STORAGE_UNAVAILABLE'))
        self.assertEqual(Store().db.accounts.count_documents({}), 0)

    def test_event_failure_rolls_back_account_creation(self):
        with patch('web.accounts._record', side_effect=AutoReconnect('audit unavailable')):
            with self.assertRaises(AutoReconnect):
                create_account('rollback-user', self.password)
        self.assertEqual(Store().db.accounts.count_documents({}), 0)

    def test_kafka_outage_retains_success_event_and_account(self):
        with patch.object(Store, 'drain', side_effect=RuntimeError('kafka unavailable')):
            response = self.signup()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Store().db.accounts.count_documents({}), 1)
        self.assertEqual(Store().db.outbox.count_documents({'payload.eventType':'signup_success'}), 1)
