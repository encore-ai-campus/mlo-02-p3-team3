"""Immediate server policy decisions. Spark observes these; it never gates requests."""
import json
import logging
import time
import sqlite3
import copy
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from .prevention_store import Store

VERSION = 'prevention-2026-11-23-v1'
SECONDS = 300
RULES = {
    'login_repeated_failure': (None, 5, '계정 누적 인증 실패'),
    'not_open_repeat': (2, 10, '오픈 전 반복 요청'),
    'reentry_repeat': (2, 10, '반복 재입장'),
    'selection_repeat': (5, 10, '반복된 잘못된 선택'),
    'purchase_limit_attempt': (None, 4, '회차 구매 한도 초과'),
}
active = ContextVar('prevention_session', default=None)
logger = logging.getLogger('ticketguard.prevention')


def scope(user, show=None, schedule=None, simulation=False):
    if not isinstance(user, str) or not 1 <= len(user) <= 256 or type(simulation) is not bool:
        raise ValueError('Unidentified policy target')
    if show is not None and (not schedule or not isinstance(show, str) or not isinstance(schedule, str)):
        raise ValueError('Unidentified booking scope')
    return json.dumps([user, show, schedule, simulation], separators=(',', ':'))


def simulated_account(username):
    from .qa_test_hooks import TEST_ACCOUNT_PATTERN
    from django.conf import settings
    match = TEST_ACCOUNT_PATTERN.fullmatch(username)
    return bool(match and 1 <= int(match.group(1)) <= settings.QA_TEST_ACCOUNT_COUNT)


class Policy:
    def __init__(self, session, target, attempt=None, clock=None):
        self.session, self.target, self.attempt = session, target, attempt
        self.now = (clock or time.time)()
        from .observation import context
        self.request_id = (context.get() or {}).get('observationRequestId') or str(uuid4())
        self.user, self.show, self.schedule, self.simulation = json.loads(target)
        self.state = session.get('policy:' + target, {'counts': {}, 'locks': {}})

    def save(self):
        self.session.put('policy:' + self.target, self.state)

    def event(self, kind, rule, **details):
        from .events import build_event
        from datetime import datetime, timezone
        seconds, threshold, reason = RULES[rule]
        base = dict(ruleId=rule, ruleVersion=VERSION, policyMode='temporary_restriction',
                    threshold=threshold, windowSeconds=seconds, reason=reason,
                    restrictionScope='account' if self.show is None else 'account_show_schedule',
                    observationRequestId=self.request_id, policyEvaluated=[rule])
        event = build_event(self.user, kind, self.attempt, showId=self.show, scheduleId=self.schedule,
            isSimulation=self.simulation,
            occurredAt=datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
            result='rejected' if kind == 'prevention_rejected' else 'success',
            details={**base, **details})
        self.session.enqueue(event)
        return event['eventId']

    def release(self, rule, reason='opened'):
        self.state['counts'].pop(rule, None)
        lock = self.state['locks'].pop(rule, None)
        if lock:
            self.event('prevention_released', rule, restrictionId=lock['id'],
                       appliedAt=lock['at'], expiresAt=lock['until'], releasedAt=self.now, releaseReason=reason)
        self.save()

    def check(self):
        from .services import FlowError
        for rule, lock in list(self.state['locks'].items()):
            if self.now >= lock['until']:
                self.event('prevention_released', rule, restrictionId=lock['id'],
                           appliedAt=lock['at'], expiresAt=lock['until'], releasedAt=self.now, releaseReason='expired')
                del self.state['locks'][rule]
        self.save()
        locks = sorted(self.state['locks'].items())
        if self.attempt is not None:
            self.attempt['policy_restricted_until'] = max((item['until'] for _, item in locks), default=0)
        if locks:
            rule, lock = locks[0]
            self.event('prevention_rejected', rule, restrictionId=lock['id'],
                       appliedAt=lock['at'], expiresAt=lock['until'])
            raise FlowError('반복 요청으로 5분 동안 이용이 제한되었어요. 잠시 후 다시 시도해 주세요.',
                            'LOGIN_LOCKED' if self.show is None else 'TEMPORARILY_RESTRICTED', 429)

    def detect(self, rule, observed, **context):
        # check() is called before each counted request, so blocked attempts never extend a lock.
        if rule in self.state['locks'] and self.state['locks'][rule]['until'] > self.now:
            return
        identity = str(uuid4())
        event_id = self.event('prevention_detected', rule, observed=observed, restrictionId=identity, **context)
        self.state['locks'][rule] = {'id': identity, 'at': self.now, 'until': self.now + SECONDS}
        if self.attempt is not None:
            self.attempt['policy_restricted_until'] = max(self.now + SECONDS, self.attempt.get('policy_restricted_until', 0))
        self.event('prevention_applied', rule, observed=observed, restrictionId=identity,
                   appliedAt=self.now, expiresAt=self.now + SECONDS, evidence=[event_id], **context)
        self.save()

    def count(self, rule, identity=None, excluded=False, **context):
        seconds, threshold, _ = RULES[rule]
        if excluded:
            self.event('prevention_evaluated', rule, observed=None, excluded=True, **context)
            return False
        if seconds is None:
            observed = self.state['counts'].get(rule, 0) + 1
            self.state['counts'][rule] = observed
        else:
            # The 10th event locks the scope, so at most ten timestamps are retained.
            from .observation import context as observation
            identity = identity or (observation.get() or {}).get('observationRequestId') or str(uuid4())
            counter_key = rule + (':' + self.attempt['id'] if rule == 'selection_repeat' else '')
            if rule == 'selection_repeat':
                # Old attempts cannot resume; retain only the current attempt's window.
                for old_key in list(self.state['counts']):
                    if old_key.startswith('selection_repeat:') and old_key != counter_key:
                        del self.state['counts'][old_key]
            values = self.state['counts'].get(counter_key, [])
            values = [item for item in values if self.now - seconds <= item[0] <= self.now]
            if not any(item[1] == identity for item in values):
                values.append([self.now, identity])
            self.state['counts'][counter_key] = values[-threshold:]
            observed = len(values)
        self.save()
        self.event('prevention_evaluated', rule, observed=observed, **context)
        if observed >= threshold:
            self.detect(rule, observed, **context)
            return True
        return False

    def quota(self, quantity):
        purchased = self.session.purchased(self.target)
        self.event('prevention_evaluated', 'purchase_limit_attempt', observed=purchased + quantity,
                   purchasedQuantity=purchased, requestedQuantity=quantity)
        if purchased + quantity > 4:
            self.detect('purchase_limit_attempt', purchased + quantity,
                        purchasedQuantity=purchased, requestedQuantity=quantity)
            from .services import FlowError
            raise FlowError('이 회차는 계정당 총 4매까지 구매할 수 있어요.', 'PURCHASE_LIMIT', 429)


def transaction(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if active.get() is not None:
            return method(self, *args, **kwargs)
        store = Store()
        try:
            with self.lock:
                user = args[0] if args else kwargs.get('user')
                attempt_before = copy.deepcopy(self.attempts.get(user))
                sold_before, remaining_before = self.sold.copy(), self.remaining
                try:
                    with store.transaction() as session:
                        token = active.set(session)
                        try:
                            return method(self, *args, **kwargs)
                        finally:
                            active.reset(token)
                except Exception as exc:
                    if not hasattr(exc, 'code'):
                        changed = self.attempts.get(user)
                        if changed and changed.get('booking_id') and changed.get('booking_id') != (attempt_before or {}).get('booking_id'):
                            self.bookings.pop(changed['booking_id'], None)
                        if attempt_before is None:
                            self.attempts.pop(user, None)
                        else:
                            self.attempts[user] = attempt_before
                        self.sold, self.remaining = sold_before, remaining_before
                    raise
        except (OSError, sqlite3.Error):
            from .services import FlowError
            raise FlowError('예매 보호 저장소를 확인할 수 없어요. 잠시 후 다시 시도해 주세요.', 'POLICY_UNAVAILABLE', 503) from None
        finally:
            try:
                store.drain()
            except Exception:
                logger.warning('prevention_outbox_pending')
    return wrapped


def login_attempt(username, authenticate):
    """Only an actual authentication failure increments the durable account count."""
    store = Store()
    try:
        with store.transaction() as session:
            simulated = simulated_account(username)
            policy = Policy(session, scope(username, simulation=simulated))
            policy.check()
            success = authenticate()
            if success:
                policy.state['counts'].pop('login_repeated_failure', None)
                policy.save()
                policy.event('prevention_evaluated', 'login_repeated_failure', observed=0, loginSucceeded=True)
                return True, False
            return False, policy.count('login_repeated_failure')
    except (OSError, sqlite3.Error):
        from .services import FlowError
        raise FlowError('로그인 보호 저장소를 확인할 수 없어요.', 'POLICY_UNAVAILABLE', 503) from None
    finally:
        try:
            store.drain()
        except Exception:
            logger.warning('prevention_outbox_pending')


def booking_policy(repo, user, show_id, attempt=None):
    from .services import SHOWS
    schedule = SHOWS[show_id]['schedule_id']
    simulated = (bool(attempt.get('scenario')) if attempt else bool(repo.scenarios.get((user, show_id)))) or simulated_account(user)
    policy = Policy(active.get(), scope(user, show_id, schedule, simulated), attempt)
    if repo.opens_at(user, show_id) <= policy.now:
        policy.release('not_open_repeat')
    policy.check()
    return policy
