"""Hourly boundary, cross-worker inventory, quota and historical replay regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch

from django.test import Client, SimpleTestCase, override_settings

from .booking_schedule import window
from .services import SHOWS, MongoRepository, FlowError

ANCHOR = '2026-11-25T14:00:00+09:00'
START = datetime.fromisoformat(ANCHOR).timestamp()
FIRST, SECOND = list(SHOWS)[:2]


@override_settings(BOOKING_OPENS_AT=ANCHOR, EVENT_PUBLISHER='mock', CAPTURE_ENABLED=False,
                   QA_CAPTCHA_FIXED_ANSWER='QA123')
class BookingScheduleTests(SimpleTestCase):
    def ready(self, user, show=FIRST):
        repo = MongoRepository()
        attempt = repo.start(user, show, 'page-' + user)
        answer = repo.challenge(user, attempt['page_id'], attempt['id'])
        repo.verify(user, attempt['page_id'], attempt['id'], answer)
        return repo, attempt

    def confirm(self, repo, attempt, user, seats=None, quantity=1):
        return repo.confirm(user, attempt['page_id'], attempt['id'], 1, seats or ['A1'], quantity)

    def test_exact_open_reset_and_five_minute_stagger(self):
        self.assertFalse(window(FIRST, START - .001)['is_open'])
        self.assertTrue(window(FIRST, START)['is_open'])
        self.assertFalse(window(SECOND, START + 299.999)['is_open'])
        self.assertTrue(window(SECOND, START + 300)['is_open'])
        self.assertEqual(window(FIRST, START + 3599.999)['opens_at'], START)
        self.assertEqual(window(FIRST, START + 3600)['opens_at'], START + 3600)
        self.assertEqual(window(SECOND, START + 3600)['opens_at'], START + 300)
        self.assertEqual(window(SECOND, START + 3900)['opens_at'], START + 3900)

    def test_server_rejects_early_start_even_when_called_directly(self):
        with patch('time.time', return_value=START + 299):
            with self.assertRaises(FlowError) as error:
                MongoRepository().start('early', SECOND, 'page')
            self.assertEqual(error.exception.code, 'NOT_OPEN')
        with patch('time.time', return_value=START + 300):
            self.assertEqual(MongoRepository().start('early', SECOND, 'page')['show_id'], SECOND)

    def test_inventory_resets_once_across_workers_and_old_attempt_expires(self):
        with patch('time.time', return_value=START + 3500):
            repo, attempt = self.ready('waiting')
            other, other_attempt = self.ready('buyer')
            booking = self.confirm(other, other_attempt, 'buyer')
            self.assertIn('A1', repo.sold_for(FIRST))
        with patch('time.time', return_value=START + 3600):
            self.assertEqual(MongoRepository().sold_for(FIRST), set())
            with self.assertRaises(FlowError) as error:
                self.confirm(repo, attempt, 'waiting')
            self.assertEqual(error.exception.code, 'EXPIRED')
            new_repo, new = self.ready('new-buyer')
            self.confirm(new_repo, new, 'new-buyer')
            self.assertEqual(MongoRepository().sold_for(FIRST), {'A1'})
            self.assertEqual([b['id'] for b in repo.history('buyer')], [booking['id']])
            # A retry of an old committed purchase returns its original booking,
            # without reserving seats or consuming this cycle's quota again.
            self.assertEqual(self.confirm(other, other_attempt, 'buyer')['id'], booking['id'])
            self.assertEqual(MongoRepository().sold_for(FIRST), {'A1'})

    def test_each_show_resets_at_its_own_opening(self):
        with patch('time.time', return_value=START + 3500):
            repo, attempt = self.ready('second-buyer', SECOND)
            self.confirm(repo, attempt, 'second-buyer')
        with patch('time.time', return_value=START + 3600):
            self.assertEqual(MongoRepository().sold_for(SECOND), {'A1'})
        with patch('time.time', return_value=START + 3900):
            self.assertEqual(MongoRepository().sold_for(SECOND), set())

    def test_quota_is_available_again_in_next_cycle(self):
        with patch('time.time', return_value=START + 100):
            repo, attempt = self.ready('repeat-buyer')
            old = self.confirm(repo, attempt, 'repeat-buyer', ['A1', 'A2', 'A3', 'A4'])
        with patch('time.time', return_value=START + 3700):
            repo, attempt = self.ready('repeat-buyer')
            new = self.confirm(repo, attempt, 'repeat-buyer', ['A1', 'A2', 'A3', 'A4'])
            self.assertNotEqual(old['cycle'], new['cycle'])
            self.assertEqual(len(repo.history('repeat-buyer')), 2)

    def test_general_stock_reset_and_empty_hours_do_not_extend_window(self):
        show = next(key for key, show in SHOWS.items() if show['kind'] == 'general')
        opening = window(show, START)['opens_at']
        with patch('time.time', return_value=opening + 100):
            repo, attempt = self.ready('general', show)
            self.confirm(repo, attempt, 'general', quantity=4)
            self.assertEqual(repo.remaining_for(show), 24)
        with patch('time.time', return_value=opening + 3 * 3600 + 1):
            self.assertEqual(MongoRepository().remaining_for(show), 28)
            self.assertEqual(window(show)['resets_at'], opening + 4 * 3600)

    def test_two_workers_cannot_sell_same_seat_after_reset(self):
        with patch('time.time', return_value=START + 3601):
            one, a = self.ready('one')
            two, b = self.ready('two')
            def buy(args):
                try:
                    return self.confirm(*args)['id']
                except FlowError as error:
                    return error.code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(buy, [(one, a, 'one'), (two, b, 'two')]))
            self.assertEqual(results.count('SEAT_SOLD'), 1)
            self.assertEqual(MongoRepository().sold_for(FIRST), {'A1'})

    def test_availability_reports_server_clock_and_latest_inventory(self):
        with patch('time.time', return_value=START + 1):
            repo, attempt = self.ready('visible')
            self.confirm(repo, attempt, 'visible')
            response = Client().get('/api/shows/' + FIRST + '/availability/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['sold'], ['A1'])
            self.assertEqual(response.json()['resets_at'], int((START + 3600) * 1000))
            self.assertEqual(response.json()['server_time'], int((START + 1) * 1000))
            self.assertIn('no-store', response.headers['Cache-Control'])
        with patch('time.time', return_value=START + 3600):
            self.assertEqual(Client().get('/api/shows/' + FIRST + '/availability/').json()['sold'], [])
