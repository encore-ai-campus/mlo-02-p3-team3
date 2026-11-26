"""포스터 공연의 목록·검색·상세 정보와 데모 예매 흐름 검사."""
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, override_settings

from .poster_catalog import WINTER_SHOWS
from .services import MockRepository, SHOWS


@override_settings(EVENT_PUBLISHER='mock', DEMO_USERNAME='poster-viewer', DEMO_PASSWORD='poster-test')
class PosterCatalogTests(SimpleTestCase):
    def login(self):
        response = self.client.post('/api/action/', data='{"action":"login","username":"poster-viewer","password":"poster-test"}', content_type='application/json')
        self.assertEqual(response.status_code, 200)

    def test_all_new_posters_are_listed_and_available_as_static_assets(self):
        response = self.client.get('/')
        for show in WINTER_SHOWS.values():
            with self.subTest(show=show['id']):
                self.assertContains(response, show['title'])
                self.assertContains(response, '/static/images/' + show['poster'])
                self.assertIsNotNone(finders.find('images/' + show['poster']))

    def test_detail_uses_poster_schedule_and_demo_booking_settings(self):
        self.login()
        for show in WINTER_SHOWS.values():
            with self.subTest(show=show['id']):
                response = self.client.get('/detail/' + show['id'] + '/')
                for value in (show['title'], show['english'], show['venue'], show['date'], show['time']):
                    self.assertContains(response, value)
                self.assertContains(response, f"1인 {show['price']}원")
                self.assertContains(response, show['kind_label'])
                self.assertContains(response, 'id="start-booking" class="button full">')
                self.assertContains(response, f"{show['date']} ({show['weekday']})")

    def test_new_genres_and_venue_search_find_matching_performance(self):
        for genre, show_id in (('클래식', 'first-snow-classics'), ('무용', 'winter-waltz')):
            response = self.client.get('/', {'genre': genre})
            self.assertEqual([s['id'] for s in response.context['shows']], [show_id])
        for genre, show_ids in (
            ('페스티벌', {'city-night', 'november-last-night'}),
            ('뮤지컬', {'starlight'}),
            ('연극', {'summer-rhythm'}),
        ):
            response = self.client.get('/', {'genre': genre})
            self.assertEqual({s['id'] for s in response.context['shows']}, show_ids)
        response = self.client.get('/', {'q': '노을 뮤직홀'})
        self.assertEqual([s['id'] for s in response.context['shows']], ['warm-soul'])

    def test_actual_weekday_is_used_for_sunday_and_monday(self):
        self.assertEqual(SHOWS['first-snow-classics']['weekday'], '일')
        self.assertEqual(SHOWS['november-last-night']['weekday'], '월')

    def test_all_performances_keep_server_schedule_and_display_weekday(self):
        for show_id, show in SHOWS.items():
            with self.subTest(show=show_id):
                self.assertEqual(show['schedule_id'], show_id + '-evening')
                self.assertIn(show['weekday'], '월화수목금토일')

    def test_original_performances_match_their_poster_text(self):
        poster_titles = {
            'warm.png': ('온기', 'WARM FREQUENCIES'),
            'winter.png': ('겨울의 문턱', 'NOVEMBER LIVE'),
            'november.png': ('입동의 소리', 'INDIE LIVE / NOVEMBER'),
        }
        self.login()
        for show in SHOWS.values():
            if show['poster'] not in poster_titles:
                continue
            with self.subTest(show=show['id']):
                self.assertEqual((show['title'], show['english']), poster_titles[show['poster']])
                self.assertTrue(show['date'].startswith('2026.11.'))
                expected_genres = {'city-night': '페스티벌', 'starlight': '뮤지컬', 'summer-rhythm': '연극'}
                self.assertEqual(show['genre'], expected_genres.get(show['id'], '콘서트'))
                response = self.client.get('/detail/' + show['id'] + '/')
                self.assertContains(response, show['title'])
                self.assertContains(response, show['english'])
                self.assertContains(response, show['venue'])

    def test_new_performances_can_complete_booking_with_server_price(self):
        for show_id, show in WINTER_SHOWS.items():
            with self.subTest(show=show_id):
                repository = MockRepository()
                attempt = repository.start('poster-viewer', show_id, 'poster-page')
                repository.advance_queue()
                answer = repository.challenge('poster-viewer', 'poster-page', attempt['id'])
                repository.verify('poster-viewer', 'poster-page', attempt['id'], answer)
                booking = repository.confirm('poster-viewer', 'poster-page', attempt['id'], 1, ['E5'], 1)
                self.assertEqual(booking['show_id'], show_id)
                self.assertEqual(booking['total'], show['price'])
