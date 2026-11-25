"""
[홍석환] 예매 시뮬레이션 업무 상태 관리
실행 저장소는 MongoDB이며, MockRepository는 명시적 테스트용 업무 로직 픽스처다.
"""
import secrets
import math
import time
from collections import deque
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from django.conf import settings
from django.utils.crypto import salted_hmac
from .events import emit
from .poster_catalog import WINTER_SHOWS
from . import prevention
from .booking_schedule import window

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
        genre='페스티벌', venue='세빛섬 컨벤션홀', date='2026.11.28', time='20:00', price=48000,
        kind='reserved', kind_label='지정석', poster='warm.png', tagline='11월의 차가운 공기를 감싸는 따뜻한 라이브.',
        description='WARM FREQUENCIES — 온기. 세빛섬 컨벤션홀에서 만나는 11월 라이브 세션입니다.')
    , 'starlight': dict(id='starlight', title='겨울의 문턱', english='NOVEMBER LIVE',
        genre='뮤지컬', venue='서울숲 야외무대', date='2026.11.29', time='18:30', price=42000,
        kind='general', kind_label='자유석', poster='winter.png', tagline='가을과 겨울 사이, 음악으로 만나는 11월.',
        description='겨울의 문턱 — NOVEMBER LIVE. 서울숲 야외무대에서 만나는 11월 라이브 공연입니다.')
    , 'summer-rhythm': dict(id='summer-rhythm', title='입동의 소리', english='INDIE LIVE / NOVEMBER',
        genre='연극', venue='잠실실내체육관', date='2026.11.30', time='19:30', price=58000,
        kind='reserved', kind_label='지정석', poster='november.png', tagline='11월을 채우는 인디 라이브 사운드.',
        description='입동의 소리 — INDIE LIVE / NOVEMBER. 잠실실내체육관에서 열리는 인디 라이브 공연입니다.')
}

SHOWS = {**WINTER_SHOWS, **SHOWS}
for show in SHOWS.values():
    # 신규 공연도 서버가 정의한 회차 ID와 날짜 기준 요일을 함께 사용한다.
    show['schedule_id'] = show['id'] + '-evening'
    show['weekday'] = '월화수목금토일'[datetime.strptime(show['date'], '%Y.%m.%d').weekday()]


class FlowError(Exception):
    def __init__(self, message, code='INVALID_STATE', status=400, redirect=None):
        self.message, self.code, self.status, self.redirect = message, code, status, redirect


INITIAL_SOLD = {'A5', 'B2', 'B8', 'D3', 'D9', 'F4', 'F8', 'H1', 'H10', 'I4'}


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
        self.sold = INITIAL_SOLD.copy()
        self.remaining = 28
        self.queue_counter = 0
        self.admission_window = None
        self.admission_count = 0
        self.opens = {}
        self.scenarios = {}
        self.cycle_inventories = {}

    def active_users(self):
        return list(self.attempts)

    def active_attempts(self):
        return self.attempts.values()

    def sold_for(self, show_id):
        if window(show_id):
            return self.cycle_inventory(show_id)['sold']
        return self.sold

    def remaining_for(self, show_id):
        if window(show_id):
            return self.cycle_inventory(show_id)['remaining']
        return self.remaining

    def cycle_inventory(self, show_id):
        sale = window(show_id)
        inventory = self.cycle_inventories.get(show_id)
        if inventory is None or inventory['cycle'] != sale['cycle']:
            inventory = dict(cycle=sale['cycle'], sold=set(), remaining=28)
            self.cycle_inventories[show_id] = inventory
        return inventory

    def sell(self, show_id, seats, quantity):
        if window(show_id):
            inventory = self.cycle_inventory(show_id)
            if SHOWS[show_id]['kind'] == 'reserved': inventory['sold'].update(seats)
            else: inventory['remaining'] -= quantity
            return
        if SHOWS[show_id]['kind'] == 'reserved': self.sold.update(seats)
        else: self.remaining -= quantity

    def set_remaining(self, show_id, quantity):
        if window(show_id):
            self.cycle_inventory(show_id)['remaining'] = quantity
            return
        self.remaining = quantity

    def current(self, user, required=True):
        a = self.attempts.get(user)
        sale = window(a['show_id']) if a else None
        if a and sale and a['stage'] not in ('complete', 'expired', 'cancelled') and a.get('cycle') != sale['cycle']:
            a['stage'], a['captcha'] = 'expired', ''
            a['reset_expired'] = True
        if a and a['stage'] not in ('complete', 'expired', 'cancelled'):
            if time.time() - a['heartbeat'] > settings.ATTEMPT_TIMEOUT_SECONDS:
                a['stage'], a['captcha'] = 'expired', ''
                emit(user, 'connection_timeout', a, result='failure', errorCode='EXPIRED', details={'reason':'heartbeat_expired','elapsedMs':round((time.time()-a['heartbeat'])*1000,2)})
        if required and (not a or a['stage'] in ('expired', 'cancelled')):
            show_id = a['show_id'] if a else 'blue-hour'
            if a and a.get('reset_expired'):
                raise FlowError('새 예매가 시작되어 좌석이 초기화되었어요. 다시 입장해 주세요.',
                                'EXPIRED', 409, '/detail/' + show_id + '/')
            raise FlowError('입장 권한이 만료되었어요. 대기열에 다시 입장해 주세요.', 'EXPIRED', 409, '/detail/' + show_id + '/')
        return a

    def opens_at(self, user, show_id):
        sale = window(show_id)
        return max(self.opens.get((user, show_id), 0), sale['opens_at'] if sale else 0)

    def advance_queue(self):
        """Global FIFO admission: at most one batch per interval, no saved slots."""
        with self.lock:
            now = time.time()
            interval = settings.BOOKING_ADMISSION_INTERVAL_SECONDS
            if self.admission_window is None or now >= self.admission_window + interval:
                self.admission_window = now
                self.admission_count = 0
            waiting = []
            for user in self.active_users():
                a = self.current(user, False)
                if a and a['stage'] == 'queue' and a.get('policy_restricted_until', 0) <= time.time():
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
            rank = sum(other['stage'] == 'queue' and other.get('policy_restricted_until', 0) <= time.time() and other['rank'] <= a['rank']
                       for other in self.active_attempts())
            available = settings.BOOKING_ADMISSION_BATCH_SIZE - self.admission_count
            if rank <= available:
                return rank, 0
            interval = settings.BOOKING_ADMISSION_INTERVAL_SECONDS
            wait = max(0, self.admission_window + interval - time.time())
            wait += ((rank - available - 1) // settings.BOOKING_ADMISSION_BATCH_SIZE) * interval
            return rank, math.ceil(wait)

    @prevention.transaction
    def start(self, user, show_id, page_id):
        if not isinstance(show_id, str) or show_id not in SHOWS:
            raise FlowError('공연을 찾을 수 없어요.')
        if not isinstance(page_id, str) or not 1 <= len(page_id) <= 80:
            raise FlowError('페이지 식별자가 필요해요.')
        with self.lock:
            policy = prevention.booking_policy(self, user, show_id)
            if self.opens_at(user, show_id) > time.time():
                policy.count('not_open_repeat')
                raise FlowError('아직 예매 시작 전이에요.', 'NOT_OPEN', 409)
            policy.event('prevention_evaluated', 'not_open_repeat', observed=0, openingState='open')
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
                     entered=time.time(), heartbeat=time.time(), rank=self.queue_counter,
                     wrong=0, captcha='', sequence=0, booking_id=None, scenario=self.scenarios.get((user,show_id)))
            sale = window(show_id)
            if sale: a['cycle'] = sale['cycle']
            a['is_simulation'] = prevention.simulated_account(user)
            self.attempts[user] = a
            policy.attempt = a
            prior_key = 'entry:' + policy.target
            prior = policy.session.get(prior_key)
            # Only server-owned same-scope causes are linked. Recovery is excluded.
            known_old = old if old and old['show_id'] == show_id and bool(
                old.get('scenario') or old.get('is_simulation')) == policy.simulation else None
            previous_id = known_old['id'] if known_old else (prior or {}).get('attempt')
            previous_status = known_old['stage'] if known_old else (prior or {}).get('status')
            cause = ('connection_expired' if previous_status == 'expired' else
                     'user_cancel' if previous_status == 'cancelled' else
                     'completed' if previous_status == 'complete' else
                     'initial' if prior is None and known_old is None else 'unconfirmed')
            policy.session.put(prior_key, {'attempt': a['id'], 'status': 'active'})
            detected = policy.count('reentry_repeat', identity=a['id'],
                excluded=cause in ('connection_expired', 'unconfirmed'),
                predecessorAttemptId=previous_id, entryCause=cause)
            emit(user, 'queue_enter', a)
            rank, wait = self.queue_status(a)
            emit(user, 'queue_register', a, details={'initialRank':max(1,rank),'estimatedWaitMs':wait*1000})
            if detected:
                raise FlowError('반복 재입장으로 5분 동안 예매가 제한되었어요.', 'TEMPORARILY_RESTRICTED', 429)
            self.advance_queue()
            return a

    @prevention.transaction
    def owned(self, user, page_id, attempt_id):
        a = self.current(user)
        if a['id'] != attempt_id:
            raise FlowError('이전 예매 요청이에요. 현재 페이지를 확인해 주세요.', 'STALE_ATTEMPT', 409)
        if not page_id or a['page_id'] != page_id:
            raise FlowError('다른 페이지에서 예매가 진행 중이에요.', 'DUPLICATE_PAGE', 409)
        prevention.booking_policy(self, user, a['show_id'], a)
        return a

    @prevention.transaction
    def tick(self, user, page_id, attempt_id):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            a['heartbeat'] = time.time()
            self.advance_queue()
            return a

    @prevention.transaction
    def challenge(self, user, page_id, attempt_id, reason='initial'):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            if a['stage'] != 'captcha':
                raise FlowError('보안 확인 단계에서만 사용할 수 있어요.')
            # QA 테스트 훅 · 고정 정답 (Issue #6)
            answer = settings.QA_CAPTCHA_FIXED_ANSWER or ''.join(secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(5))
            a['captcha'] = salted_hmac('ticketguard-captcha:' + a['id'], answer, algorithm='sha256').hexdigest()
            if reason=='user_refresh': emit(user,'captcha_refresh',a,details={'reason':'user_refresh'})
            return answer

    @prevention.transaction
    def verify(self, user, page_id, attempt_id, answer):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            if a['stage'] != 'captcha':
                raise FlowError('보안 확인 단계가 아니에요.')
            if not a['captcha'] or not secrets.compare_digest(salted_hmac('ticketguard-captcha:' + a['id'], answer.strip().upper(), algorithm='sha256').hexdigest(), a['captcha']):
                a['wrong'] += 1
                counts={'wrongCount':a['wrong'],'remainingAttempts':max(0,5-a['wrong'])}
                emit(user, 'captcha_wrong', a, result='failure', errorCode='CAPTCHA_WRONG',details=counts)
                if a['wrong'] >= 5:
                    emit(user,'captcha_limit_reached',a,result='failure',errorCode='CAPTCHA_LIMIT',details=counts)
                    a['stage'], a['captcha'] = 'cancelled', ''
                    raise FlowError('입력 가능 횟수를 초과했어요. 대기열에 다시 입장해 주세요.', 'CAPTCHA_LIMIT', 409, '/detail/' + a['show_id'] + '/')
                raise FlowError(f"입력한 문자가 일치하지 않아요 · 남은 기회 {5-a['wrong']}회", 'CAPTCHA_WRONG')
            a['stage'], a['captcha'] = 'select', ''
            a['heartbeat'] = time.time()
            emit(user, 'captcha_success', a)
            return a

    @prevention.transaction
    def cancel(self, user, page_id, attempt_id, reason='user_cancel'):
        with self.lock:
            a = self.owned(user, page_id, attempt_id)
            if a['stage'] != 'complete':
                a['stage'], a['captcha'] = 'cancelled', ''
                policy = prevention.booking_policy(self, user, a['show_id'], a)
                policy.session.put('entry:' + policy.target, {'attempt': a['id'], 'status': 'cancelled'})
                emit(user, 'queue_cancel', a, details={'reason':reason,'elapsedMs':round((time.time()-a['entered'])*1000,2)})
            return a

    @prevention.transaction
    def confirm(self, user, page_id, attempt_id, sequence, seats, quantity):
        started=time.time()
        with self.lock:
            durable = prevention.active.get().replay(attempt_id) if isinstance(attempt_id, str) else None
            if durable and durable.get('user') == user and durable.get('page_id') == page_id:
                # A committed purchase can be replayed after process restart without
                # recreating the attempt, changing inventory or debiting the quota.
                recovered = dict(id=attempt_id, page_id=durable['page_id'], show_id=durable['show_id'],
                                 scenario=durable.get('scenario'), is_simulation=durable['is_simulation'])
                policy = prevention.Policy(prevention.active.get(), prevention.scope(user,
                    durable['show_id'], durable['schedule_id'], durable['is_simulation']), recovered)
                if self.opens_at(user, durable['show_id']) <= policy.now:
                    policy.release('not_open_repeat')
                policy.check()
                self.bookings[durable['id']] = durable
                from .events import build_event
                policy.session.enqueue(build_event(user, 'booking_confirm_replayed', recovered,
                    details=self.booking_details(durable, started)))
                return durable
            a = self.owned(user, page_id, attempt_id)
            policy = prevention.booking_policy(self, user, a['show_id'], a)
            durable = policy.session.replay(a['id'])
            if durable:
                self.bookings[durable['id']] = durable
                a['booking_id'], a['stage'] = durable['id'], 'complete'
                emit(user, 'booking_confirm_replayed', a, details=self.booking_details(durable, started))
                return durable
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
                selection_error = None
                if show['kind'] == 'reserved':
                    valid = {f'{r}{c}' for r in 'ABCDEFGHIJ' for c in range(1, 11)}
                    if not isinstance(seats, list) or any(not isinstance(s, str) for s in seats):
                        selection_error = 'invalid_seat'
                        raise FlowError('좌석 선택을 확인해 주세요.')
                    if not 1 <= len(seats) <= 4 or len(set(seats)) != len(seats) or not set(seats) <= valid:
                        selection_error = ('quantity_range' if not 1 <= len(seats) <= 4 else
                                           'duplicate_seat' if len(set(seats)) != len(seats) else 'invalid_seat')
                        raise FlowError('좌석은 1~4석까지 선택할 수 있어요.', 'SELECTION_LIMIT')
                    if self.sold_for(show['id']).intersection(seats):
                        raise FlowError('선택한 좌석이 먼저 판매되었어요. 다른 좌석을 선택해 주세요.', 'SEAT_SOLD', 409)
                    quantity = len(seats)
                else:
                    if type(quantity) is not int or not 1 <= quantity <= 4:
                        selection_error = 'quantity_range'
                        raise FlowError('관람 인원은 1~4명까지 선택할 수 있어요.', 'SELECTION_LIMIT')
                    if quantity > self.remaining_for(show['id']):
                        raise FlowError(f"잔여 수량이 {self.remaining_for(show['id'])}명으로 변경되었어요. 인원을 수정해 주세요.", 'INSUFFICIENT_STOCK', 409)
                    seats = []
                policy.event('prevention_evaluated', 'selection_repeat', observed=0, selectionValid=True)
                policy.quota(quantity)
                booking_id = 'TG-' + uuid4().hex[:10].upper()
                b = dict(id=booking_id, user=user, show_id=show['id'], seats=seats,
                         quantity=quantity, total=show['price'] * quantity,
                         created_at=datetime.now(timezone.utc).isoformat(),
                         page_id=page_id, schedule_id=show['schedule_id'], is_simulation=policy.simulation,
                         booking_attempt_id=a['id'], scenario=a.get('scenario'))
                if a.get('cycle'): b['cycle'] = a['cycle']
                policy.session.purchase(policy.target, a['id'], b)
                policy.session.put('entry:' + policy.target, {'attempt': a['id'], 'status': 'complete'})
                self.sell(show['id'], seats, quantity)
                self.bookings[booking_id] = b
                a['booking_id'], a['stage'] = booking_id, 'complete'
                from .events import build_event
                policy.session.enqueue(build_event(user, 'booking_confirm_success', a,
                    requestSequence=sequence, details=self.booking_details(b, started)))
                return b
            except FlowError as e:
                if selection_error:
                    policy.count('selection_repeat', selectionError=selection_error)
                    requested = (len(seats) if isinstance(seats, list) and all(isinstance(s, str) for s in seats)
                                 and len(set(seats)) == len(seats) and set(seats) <= valid else None) if show['kind'] == 'reserved' else quantity
                    if selection_error == 'quantity_range' and type(requested) is int and requested > 4:
                        # Preserve the existing selection error response while recording
                        # the independently exceeded account quota and its restriction.
                        try:
                            policy.quota(requested)
                        except FlowError as quota_error:
                            if quota_error.code != 'PURCHASE_LIMIT':
                                raise
                elif e.code in ('SEAT_SOLD', 'INSUFFICIENT_STOCK'):
                    policy.event('prevention_evaluated', 'selection_repeat', observed=None,
                                 excluded=True, selectionError=e.code)
                e.logged=True
                emit(user, 'booking_confirm_fail', a, requestSequence=sequence, result='failure', errorCode=e.code,
                     details={'seatType':SHOWS[a['show_id']]['kind'],'durationMs':round((time.time()-started)*1000,2),
                              'selectionError':selection_error})
                raise

    @staticmethod
    def booking_details(booking,started):
        return {'bookingId':booking['id'],'seatType':SHOWS[booking['show_id']]['kind'],'quantity':booking['quantity'],
                'totalAmount':booking['total'],'durationMs':round((time.time()-started)*1000,2)}

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
                emit(user,'queue_admitted',a,details={'elapsedMs':round((time.time()-a['entered'])*1000,2)})
            elif name == 'expire' and a['stage'] != 'complete':
                a['heartbeat'] = time.time() - settings.ATTEMPT_TIMEOUT_SECONDS - 1
            elif name == 'four_wrong' and a['stage'] == 'captcha':
                a['wrong'] = 4
            elif name == 'collision' and a['stage'] == 'select' and a['show_id'] == 'blue-hour':
                valid = {f'{r}{c}' for r in 'ABCDEFGHIJ' for c in range(1, 11)}
                if not isinstance(seats, list) or not seats or any(not isinstance(s, str) or s not in valid for s in seats):
                    raise FlowError('충돌을 확인할 좌석을 먼저 선택해 주세요.')
                self.sell(a['show_id'], [seats[-1]], 1)
            elif name == 'shortage' and a['stage'] == 'select' and a['show_id'] == 'open-air':
                self.set_remaining(a['show_id'], 1)
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


from .booking_store import MongoBookingMixin, durable


class MongoRepository(MongoBookingMixin, MockRepository):
    """Production workflow: every public operation uses authoritative MongoDB state."""


for _method in ('current', 'opens_at', 'advance_queue', 'queue_status', 'booking', 'scenario', 'scenario_context'):
    setattr(MongoRepository, _method, durable(getattr(MockRepository, _method)))

repo = MongoRepository()
