"""관측 추가가 업무 제한을 바꾸지 않고 비밀값을 복제하지 않는지 검사."""
import json

from django.test import Client, SimpleTestCase, override_settings

from .events import publisher
from .services import repo


@override_settings(EVENT_PUBLISHER='mock', CAPTURE_ENABLED=False)
class ObservationTests(SimpleTestCase):
    def setUp(self):
        with override_settings(QA_RESET_ENABLED=True):
            repo.reset()
        publisher.receipts.clear()
        publisher.records.clear()
        self.client = Client()
        session = self.client.session
        session['user'] = 'observation-user'
        session['role'] = 'customer'
        session.save()
        self.client.cookies['sessionid'] = session.session_key

    def post(self, data):
        return self.client.post('/api/action/', json.dumps(data), content_type='application/json')

    def events(self):
        return [entry['event'] for entry in publisher.receipts.values()]

    def test_received_request_correlates_rejection_without_copying_body(self):
        attempt=repo.start('observation-user','blue-hour','page')
        response=self.post(dict(action='confirm',pageId='page',attemptId=attempt['id'],
                                sequence=1,password='private-sentinel',answer='secret-answer'))
        self.assertEqual(response.status_code,403)
        observed=[e for e in self.events() if e['eventType']=='ticketing_action_received'][-1]
        rejected=[e for e in self.events() if e['eventType']=='booking_confirm_rejected'][-1]
        self.assertEqual(observed['details']['observationRequestId'],rejected['details']['observationRequestId'])
        self.assertEqual(observed['bookingAttemptId'],attempt['id'])
        self.assertNotIn('private-sentinel',json.dumps(observed))
        self.assertNotIn('secret-answer',json.dumps(observed))
        self.assertNotEqual(attempt['stage'],'cancelled')

    def test_new_requests_have_new_server_ids_and_untrusted_attempt_is_not_joined(self):
        for _ in range(2):
            self.post(dict(action='confirm',pageId='page',attemptId='forged',sequence=1))
        events=[e for e in self.events() if e['eventType']=='ticketing_action_received']
        self.assertEqual(len(events),2)
        self.assertNotEqual(events[0]['details']['observationRequestId'],events[1]['details']['observationRequestId'])
        self.assertTrue(all(e['bookingAttemptId'] is None and not e['details']['contextKnown'] for e in events))

    def test_existing_captcha_limit_has_one_correlated_action(self):
        attempt=repo.start('observation-user','blue-hour','page')
        repo.challenge('observation-user','page',attempt['id'])
        with repo.lock:
            repo.attempts['observation-user']['wrong']=4
        response=self.post(dict(action='verify',pageId='page',attemptId=attempt['id'],answer='wrong'))
        self.assertEqual(response.json()['code'],'CAPTCHA_LIMIT')
        limits=[e for e in self.events() if e['eventType']=='captcha_limit_reached']
        self.assertEqual(len(limits),1)
        self.assertEqual(limits[0]['details']['wrongCount'],5)
        self.assertTrue(limits[0]['details']['observationRequestId'])
        with repo.lock:
            self.assertEqual(repo.attempts['observation-user']['stage'],'cancelled')
