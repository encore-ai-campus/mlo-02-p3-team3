import json
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from .login_guard import login_guard


@override_settings(EVENT_PUBLISHER='mock', LOGIN_MAX_FAILURES=5, LOGIN_LOCK_SECONDS=300)
class LoginLimitTests(SimpleTestCase):
    def setUp(self):
        login_guard.reset()

    def login(self, username='ticketguard', password='wrong'):
        return self.client.post('/api/action/', json.dumps(dict(action='login', username=username, password=password)),
                                content_type='application/json')

    def test_failures_show_running_count_then_lock(self):
        for count in range(1, 5):
            response = self.login()
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()['code'], 'LOGIN_FAILED')
            self.assertEqual(response.json()['failureCount'], count)
            self.assertIn(f'로그인 오류 {count}회', response.json()['error'])
        locked = self.login()
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json()['code'], 'LOGIN_LOCKED')
        self.assertIn('잠시 후 다시 시도', locked.json()['error'])
        self.assertNotIn('retryAfter', locked.json())  # no remaining time is exposed

    def test_locked_account_rejects_correct_password_until_lock_expires(self):
        # Browser guard uses monotonic time; durable account locks use wall time.
        # Advance both clocks while preserving the original 429 -> 200 assertions.
        with patch('web.login_guard.time.monotonic', return_value=1000.0), patch('web.prevention.time.time', return_value=1000.0):
            for _ in range(5):
                self.login()
            self.assertEqual(self.login(password='ticketguard-demo').status_code, 429)
        with patch('web.login_guard.time.monotonic', return_value=1301.0), patch('web.prevention.time.time', return_value=1301.0):
            self.assertEqual(self.login(password='ticketguard-demo').status_code, 200)

    def test_count_is_per_browser_across_usernames(self):
        for count, username in enumerate(['typo-a', 'typo-b', 'ticketguard', 'guest'], start=1):
            self.assertEqual(self.login(username=username).json()['failureCount'], count)
        self.assertEqual(self.login(username='another').status_code, 429)
        self.assertEqual(self.login(username='guest', password='ticketguard-demo').status_code, 429)
        other_browser = self.client_class()
        response = other_browser.post('/api/action/', json.dumps(dict(action='login', username='ticketguard', password='ticketguard-demo')),
                                      content_type='application/json')
        self.assertEqual(response.status_code, 200)

    def test_success_resets_count_and_cookie_is_stable(self):
        first = self.login()
        cookie = first.cookies['tg_login']
        self.assertTrue(cookie['httponly'])
        self.assertNotIn('tg_login', self.login().cookies)  # issued once, then reused
        self.assertEqual(self.login(password='ticketguard-demo').status_code, 200)
        self.assertEqual(self.login().json()['failureCount'], 1)

    def test_tampered_cookie_gets_a_new_count(self):
        self.login(); self.login()
        self.client.cookies['tg_login'] = 'forged-value'
        response = self.login()
        self.assertEqual(response.json()['failureCount'], 1)
        self.assertIn('tg_login', response.cookies)

    def test_browser_window_reset_does_not_erase_account_failures(self):
        with patch('web.login_guard.time.monotonic', return_value=1000.0):
            for _ in range(4):
                self.login()
        with patch('web.login_guard.time.monotonic', return_value=1400.0):
            response = self.login()
            # Preserve the old browser window reset, and independently verify the
            # newly required lifetime account counter and actual restriction.
            self.assertEqual(next(iter(login_guard.entries.values()))['failures'], 1)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()['code'], 'LOGIN_LOCKED')
            from .prevention import scope
            from .prevention_store import Store
            with Store().transaction() as session:
                state = session.get('policy:' + scope('ticketguard'))
            self.assertEqual(state['counts']['login_repeated_failure'], 5)
            self.assertEqual(len(state['locks']), 1)

    def test_events_for_dashboard(self):
        with patch('web.views.emit') as emit:
            for _ in range(6):
                self.login()
        calls = [(c.args[1], c.kwargs.get('errorCode'), c.kwargs.get('details')) for c in emit.call_args_list]
        self.assertEqual(calls[0], ('login_fail', 'INVALID_CREDENTIALS', {'failureCount': 1, 'maxFailures': 5}))
        self.assertIn(('login_fail', 'LOGIN_LOCKED', {'reason': 'limit_reached', 'failureCount': 5}), calls)
        self.assertEqual(calls[-1], ('login_fail', 'LOGIN_LOCKED', {'reason': 'locked'}))
        self.assertTrue(all(c.args[0] is None for c in emit.call_args_list))  # unverified user is never recorded

    def test_login_page_has_lock_notice(self):
        response = self.client.get('/login/')
        self.assertContains(response, 'id="login-lock"')
        self.assertContains(response, '로그인이 잠겼어요')
