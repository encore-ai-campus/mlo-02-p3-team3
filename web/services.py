"""Demo-only service, protected by one process lock. Never use with multiple workers."""
import secrets
import math
import time
from collections import deque
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from django.conf import settings
from .events import emit
from .poster_catalog import WINTER_SHOWS

SHOWS = {
    'blue-hour': dict(id='blue-hour', title='온기', english='WARM FREQUENCIES',
        genre='콘서트', venue='한빛아트홀', date='2026.11.25', time='19:00',
        price=55000, kind='reserved', kind_label='지정석', poster='warm.png',
        tagline='11월의 차가운 공기를 감싸는 따뜻한 라이브.',
        description='WARM FREQUENCIES — 온기. 한빛아트홀에서 만나는 11월 라이브 세션입니다.'),
    'open-air': dict(id='open-air', title='겨울의 문턱', english='NOVEMBER LIVE',
        genre='콘서트', venue='리버사이드 파크', date='2026.11.26', time='18:00',
        price=35000, kind='general', kind_label='자유석', poster='winter.png',
        tagline='가을과 겨울 사이, 음악으로 만나는 11월.',
        description='겨울의 문턱 — NOVEMBER LIVE. 리버사이드 파크에서 만나는 11월 라이브 공연입니다.')
    , 'spring-concert': dict(id='spring-concert', title='입동의 소리', english='INDIE LIVE / NOVEMBER',
        genre='콘서트', venue='올림픽공원 올림픽홀', date='2026.11.27', time='19:00', price=65000,
        kind='reserved', kind_label='지정석', poster='november.png', tagline='11월을 채우는 인디 라이브 사운드.',
        description='입동의 소리 — INDIE LIVE / NOVEMBER. 올림픽공원 올림픽홀에서 열리는 인디 라이브 공연입니다.')
    , 'city-night': dict(id='city-night', title='온기', english='WARM FREQUENCIES',
        genre='콘서트', venue='세빛섬 컨벤션홀', date='2026.11.28', time='20:00', price=48000,
        kind='reserved', kind_label='지정석', poster='warm.png', tagline='11월의 차가운 공기를 감싸는 따뜻한 라이브.',
        description='WARM FREQUENCIES — 온기. 세빛섬 컨벤션홀에서 만나는 11월 라이브 세션입니다.')
    , 'starlight': dict(id='starlight', title='겨울의 문턱', english='NOVEMBER LIVE',
        genre='콘서트', venue='서울숲 야외무대', date='2026.11.29', time='18:30', price=42000,
        kind='general', kind_label='자유석', poster='winter.png', tagline='가을과 겨울 사이, 음악으로 만나는 11월.',
        description='겨울의 문턱 — NOVEMBER LIVE. 서울숲 야외무대에서 만나는 11월 라이브 공연입니다.')
    , 'summer-rhythm': dict(id='summer-rhythm', title='입동의 소리', english='INDIE LIVE / NOVEMBER',
        genre='콘서트', venue='잠실실내체육관', date='2026.11.30', time='19:30', price=58000,
        kind='reserved', kind_label='지정석', poster='november.png', tagline='11월을 채우는 인디 라이브 사운드.',
        description='입동의 소리 — INDIE LIVE / NOVEMBER. 잠실실내체육관에서 열리는 인디 라이브 공연입니다.')
}

SHOWS = {**WINTER_SHOWS, **SHOWS}
for show in SHOWS.values():
    show['weekday'] = '월화수목금토일'[datetime.strptime(show['date'], '%Y.%m.%d').weekday()]


class FlowError(Exception):
    def __init__(self, message, code='INVALID_STATE', status=400, redirect=None):
        self.message, self.code, self.status, self.redirect = message, code, status, redirect


class MockRepository:
    def __init__(self):
        self.lock = RLock()
        self.reset()

    def reset(self):
        """처음 상태로 · QA 테스트 훅 초기화도 사용 (Issue #6)"""
        with self.lock:
            self._set_initial_state()

    def _set_initial_state(self):
        self.attempts = {}
        self.bookings = {}
        self.sold = {'A5', 'B2', 'B8', 'D3', 'D9', 'F4', 'F8', 'H1', 'H10', 'I4'}
        self.remaining = 28
        self.queue_counter = 0
        self.admission_window = None
        self.admission_count = 0
        self.opens = {}
        self.scenarios = {}

    def current(self, user, required=True):
        a = self.attempts.get(user)
        if a and a['stage'] not in ('complete', 'expired', 'cancelled'):
            if time.monotonic() - a['heartbeat'] > settings.ATTEMPT_TIMEOUT_SECONDS:
                a['stage'], a['captcha'] = 'expired', ''
                emit(user, 'connection_timeout', a, result='failure', errorCode='EXPIRED', details={'reason':'heartbeat_expired','elapsedMs':round((time.monotonic()-a['heartbeat'])*1000,2)})
        if required and (not a or a['stage'] in ('expired', 'cancelled')):
            show_id = a['show_id'] if a else 'blue-hour'
            raise FlowError('입장 권한이 만료되었어요. 대기열에 다시 입장해 주세요.', 'EXPIRED', 409, '/detail/' + show_id + '/')
        return a

    def opens_at(self, user, show_id):
        return self.opens.get((user, show_id), 0)

    def advance_queue(self):
        """Global FIFO admission: at most one batch per interval, no saved slots."""
        with self.lock:
            now = time.monotonic()
            interval = settings.BOOKING_ADMISSION_INTERVAL_SECONDS
            if self.admission_window is None or now >= self.admission_window + interval:
                self.admission_window = now
                self.admission_count = 0
            waiting = []
            for user in list(self.attempts):
                a = self.current(user, False)
                if a and a['stage'] == 'queue':
                    waiting.append((user, a))
            waiting.sort(key=lambda item: item[1]['rank'])
            available = settings.BOOKING_ADMISSION_BATCH_SIZE - self.admission_count
            for user, a in waiting[:max(0, available)]:
                a['stage'] = 'captcha'
                self.admission_count += 1
                emit(user, 'queue_admitted', a, details={'elapsedMs':round((now-a['entered'])*1000,2)})

    def queue_status(self, a):
        with self.lock:
            if a['stage'] != 'queue':
                return 0, 0
            rank = sum(other['stage'] == 'queue' and other['rank'] <= a['rank']
                       for other in self.attempts.values())
            available = settings.BOOKING_ADMISSION_BATCH_SIZE - self.admission_count
            if rank <= available:
                return rank, 0
            interval = settings.BOOKING_ADMISSION_INTERVAL_SECONDS
            wait = max(0, self.admission_window + interval - time.monotonic())
            wait += ((rank - available - 1) // settings.BOOKING_ADMISSION_BATCH_SIZE) * interval
            return rank, math.ceil(wait)

    def start(self, user, show_id, page_id):
        if not isinstance(show_id, str) or show_id not in SHOWS:
            raise FlowError('공연을 찾을 수 없어요.')
        if not isinstance(page_id, str) or not 1 <= len(page_id) <= 80:
            raise FlowError('페이지 식별자가 필요해요.')
        with self.lock:
            if self.opens_at(user, show_id) > time.time():
                raise FlowError('아직 예매 시작 전이에요.', 'NOT_OPEN', 409)
            old = self.current(user, False)
            if old and old['stage'] not in ('complete', 'cancelled', 'expired'):
                if old['page_id'] != page_id:
                    raise FlowError('다른 페이지에서 예매가 진행 중이에요. 기존 페이지를 이용해 주세요.', 'DUPLICATE_PAGE', 409)
                if old['show_id'] != show_id:
                    raise FlowError('진행 중인 예매를 먼저 취소해 주세요.', 'ACTIVE_ATTEMPT', 409)
                return old
            self.advance_queue()
            self.queue_counter += 1
            a = dict(id=str(uuid4()), show_id=show_id, page_id=page_id, stage='queue',
                     entered=time.monotonic(), heartbeat=time.monotonic(), rank=self.queue_counter,
                     wrong=0, captcha='', sequence=0, booking_id=None, scenario=self.scenarios.get((user,show_id)))
            self.attempts[user] = a
            emit(user, 'queue_enter', a)
            rank, wait = self.queue_status(a)
            emit(user, 'queue_register', a, details={'initialRank':max(1,rank),'estimatedWaitMs':wait*1000})
            self.advance_queue()
            return a

    def owned(self, user, page_id, attempt_id):
        a = self.current(user)
        if a['id'] != attempt_id:
            raise FlowError('이전 예매 요청이에요. 현재 페이지를 확인해 주세요.', 'STALE_ATTEMPT', 409)
        if not page_id or a['page_id'] != page_id:
            raise FlowError('다른 페이지에서 예매가 진행 중이에요.', 'DUPLICATE_PAGE', 409)
        return a

    def tick(self, user, page_id, attempt_id):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            a['heartbeat'] = time.monotonic()
            self.advance_queue()
            return a

    def challenge(self, user, page_id, attempt_id, reason='initial'):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            if a['stage'] != 'captcha':
                raise FlowError('보안 확인 단계에서만 사용할 수 있어요.')
            # QA 테스트 훅 · 고정 정답 (Issue #6)
            a['captcha'] = settings.QA_CAPTCHA_FIXED_ANSWER or ''.join(secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(5))
            if reason=='user_refresh': emit(user,'captcha_refresh',a,details={'reason':'user_refresh'})
            return a['captcha']

    def verify(self, user, page_id, attempt_id, answer):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            if a['stage'] != 'captcha':
                raise FlowError('보안 확인 단계가 아니에요.')
            if not a['captcha'] or not secrets.compare_digest(answer.strip().upper(), a['captcha']):
                a['wrong'] += 1
                counts={'wrongCount':a['wrong'],'remainingAttempts':max(0,5-a['wrong'])}
                emit(user, 'captcha_wrong', a, result='failure', errorCode='CAPTCHA_WRONG',details=counts)
                if a['wrong'] >= 5:
                    emit(user,'captcha_limit_reached',a,result='failure',errorCode='CAPTCHA_LIMIT',details=counts)
                    a['stage'], a['captcha'] = 'cancelled', ''
                    raise FlowError('입력 가능 횟수를 초과했어요. 대기열에 다시 입장해 주세요.', 'CAPTCHA_LIMIT', 409, '/detail/' + a['show_id'] + '/')
                raise FlowError(f"입력한 문자가 일치하지 않아요 · 남은 기회 {5-a['wrong']}회", 'CAPTCHA_WRONG')
            a['stage'], a['captcha'] = 'select', ''
            a['heartbeat'] = time.monotonic()
            emit(user, 'captcha_success', a)
            return a

    def cancel(self, user, page_id, attempt_id, reason='user_cancel'):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            if a['stage'] != 'complete':
                a['stage'], a['captcha'] = 'cancelled', ''
                emit(user, 'queue_cancel', a, details={'reason':reason,'elapsedMs':round((time.monotonic()-a['entered'])*1000,2)})
            return a

    def confirm(self, user, page_id, attempt_id, sequence, seats, quantity):
        started=time.monotonic()
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            if a['booking_id']:
                existing=self.bookings[a['booking_id']]
                emit(user,'booking_confirm_replayed',a,details=self.booking_details(existing,started))
                return existing
            if a['stage'] != 'select':
                raise FlowError('보안 확인을 완료해 주세요.', 'NOT_VERIFIED', 403)
            if type(sequence) is not int or sequence < 1:
                raise FlowError('요청 순서가 올바르지 않아요.')
            if sequence <= a['sequence']:
                raise FlowError('이전 요청은 처리하지 않았어요. 다시 확인해 주세요.', 'STALE_REQUEST', 409)
            a['sequence'] = sequence
            emit(user, 'booking_confirm_request', a, requestSequence=sequence)
            try:
                show = SHOWS[a['show_id']]
                if show['kind'] == 'reserved':
                    valid = {f'{r}{c}' for r in 'ABCDEFGHIJ' for c in range(1, 11)}
                    if not isinstance(seats, list) or any(not isinstance(s, str) for s in seats):
                        raise FlowError('좌석 선택을 확인해 주세요.')
                    if not 1 <= len(seats) <= 4 or len(set(seats)) != len(seats) or not set(seats) <= valid:
                        raise FlowError('좌석은 1~4석까지 선택할 수 있어요.', 'SELECTION_LIMIT')
                    if self.sold.intersection(seats):
                        raise FlowError('선택한 좌석이 먼저 판매되었어요. 다른 좌석을 선택해 주세요.', 'SEAT_SOLD', 409)
                    quantity = len(seats)
                else:
                    if type(quantity) is not int or not 1 <= quantity <= 4:
                        raise FlowError('관람 인원은 1~4명까지 선택할 수 있어요.', 'SELECTION_LIMIT')
                    if quantity > self.remaining:
                        raise FlowError(f'잔여 수량이 {self.remaining}명으로 변경되었어요. 인원을 수정해 주세요.', 'INSUFFICIENT_STOCK', 409)
                    seats = []
                booking_id = 'TG-' + uuid4().hex[:10].upper()
                b = dict(id=booking_id, user=user, show_id=show['id'], seats=seats,
                         quantity=quantity, total=show['price'] * quantity,
                         created_at=datetime.now(timezone.utc).isoformat())
                if show['kind'] == 'reserved':
                    self.sold.update(seats)
                else:
                    self.remaining -= quantity
                self.bookings[booking_id] = b
                a['booking_id'], a['stage'] = booking_id, 'complete'
                emit(user, 'booking_confirm_success', a, requestSequence=sequence,details=self.booking_details(b,started))
                return b
            except FlowError as e:
                e.logged=True
                emit(user, 'booking_confirm_fail', a, requestSequence=sequence, result='failure', errorCode=e.code,
                     details={'seatType':SHOWS[a['show_id']]['kind'],'durationMs':round((time.monotonic()-started)*1000,2)})
                raise

    @staticmethod
    def booking_details(booking,started):
        return {'bookingId':booking['id'],'seatType':SHOWS[booking['show_id']]['kind'],'quantity':booking['quantity'],
                'totalAmount':booking['total'],'durationMs':round((time.monotonic()-started)*1000,2)}

    def booking(self, user, identifier):
        b = self.bookings.get(identifier)
        if not b or b['user'] != user:
            raise FlowError('예매 내역을 찾을 수 없어요.', 'NOT_FOUND', 404)
        return b

    def history(self, user):
        return [b for b in reversed(list(self.bookings.values())) if b['user'] == user]

    def scenario(self, user, page_id, attempt_id, name, seats, show_id):
        with self.lock:
            if name in ('open_later', 'open_now'):
                if not isinstance(show_id, str) or show_id not in SHOWS:
                    raise FlowError('공연을 선택해 주세요.')
                self.opens[user, show_id] = time.time() + 30 if name == 'open_later' else 0
                self.scenarios[user,show_id]=name
                emit(user,'scenario_executed',showId=show_id,scheduleId=show_id+'-evening',isSimulation=True,scenarioName=name,details={'scenario':name})
                return
            a = self.owned(user, page_id, attempt_id)
            if name == 'admit' and a['stage'] == 'queue':
                a['stage'] = 'captcha'
                emit(user,'queue_admitted',a,details={'elapsedMs':round((time.monotonic()-a['entered'])*1000,2)})
            elif name == 'expire' and a['stage'] != 'complete':
                a['heartbeat'] = time.monotonic() - settings.ATTEMPT_TIMEOUT_SECONDS - 1
            elif name == 'four_wrong' and a['stage'] == 'captcha':
                a['wrong'] = 4
            elif name == 'collision' and a['stage'] == 'select' and a['show_id'] == 'blue-hour':
                valid = {f'{r}{c}' for r in 'ABCDEFGHIJ' for c in range(1, 11)}
                if not isinstance(seats, list) or not seats or any(not isinstance(s, str) or s not in valid for s in seats):
                    raise FlowError('충돌을 확인할 좌석을 먼저 선택해 주세요.')
                self.sold.add(seats[-1])
            elif name == 'shortage' and a['stage'] == 'select' and a['show_id'] == 'open-air':
                self.remaining = 1
            elif name in ('disconnect','verification_outage','repeat_confirm','old_confirm'):
                pass
            else:
                raise FlowError('현재 단계에서 사용할 수 없는 시나리오예요.')
            a['scenario']=name
            emit(user,'scenario_executed',a,details={'scenario':name})

    def scenario_context(self,user,show_id=None):
        a=self.attempts.get(user)
        if a and a['stage'] not in ('complete','cancelled','expired') and (show_id is None or a['show_id']==show_id): return a.get('scenario')
        return self.scenarios.get((user,show_id))


repo = MockRepository()
