"""서버 관측의 최소 투영과 재실행 가능한 기간별 규칙 평가."""
import hashlib
import json
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from user_flow.events import body, stamp

LABELS = {'overlap': '응답 전 중첩', 'retry': '재요청 시도', 'pages': '다중 페이지 계정',
          'fast': '빠른 완료', 'purchases': '단시간 복수 구매', 'limit': '한도 초과'}
KINDS = {'server_request', 'queue_enter', 'queue_register', 'booking_confirm_request', 'booking_confirm_success', 'booking_confirm_fail'}


@dataclass(frozen=True)
class Policy:
    overlap: bool | None = None
    retry: float | None = None
    pages: float | None = None
    fast: float | None = None
    purchases: float | None = None
    limit: int | None = None

    @classmethod
    def load(cls):
        values = {}
        for key, suffix in [('retry', 'RETRY_SECONDS'), ('pages', 'PAGES_SECONDS'),
                            ('fast', 'FAST_SECONDS'), ('purchases', 'PURCHASE_SECONDS'), ('limit', 'ACCOUNT_LIMIT')]:
            value = os.environ.get('ANOMALY_' + suffix)
            if value:
                number = float(value)
                if not math.isfinite(number) or not 0 < number <= (10000 if key == 'limit' else 3600):
                    raise ValueError('Invalid anomaly policy: ' + suffix)
                if key == 'limit' and number != int(number):
                    raise ValueError('Account limit must be an integer')
                values[key] = int(number) if key == 'limit' else number
        overlap = os.environ.get('ANOMALY_OVERLAP_ENABLED', '')
        if overlap not in ('', '0', '1'):
            raise ValueError('ANOMALY_OVERLAP_ENABLED must be 0 or 1')
        if overlap == '1':
            values['overlap'] = True
        return cls(**values)

    def version(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:20]


def project(event):
    if event.get('source') != 'server' or event['eventType'] not in KINDS:
        return None
    details = event['details']
    if not isinstance(details, dict):
        return None
    kind = event['eventType']
    request = details.get('request')
    request = request if isinstance(request, dict) else {}
    payload = body(request.get('body'))
    if kind == 'server_request':
        url = request.get('url')
        if not isinstance(url, str) or payload.get('action') != 'confirm':
            return None
        try:
            if urlsplit(url).path != '/api/action/':
                return None
        except ValueError:
            return None
    at = stamp(event['occurredAt'])
    timing = details.get('timing')
    response = details.get('response')
    ms = timing.get('observationDurationMs') if isinstance(timing, dict) else None
    duration = ms / 1000 if type(ms) in (int, float) and math.isfinite(ms) and 0 <= ms <= 86400000 else None
    quantity = details.get('quantity')
    row = dict(id=event['eventId'], time=at, received=stamp(event['receivedAt']), kind=kind,
               user=event.get('userId'), attempt=event.get('bookingAttemptId') or payload.get('attemptId'),
               page=event.get('pageInstanceId') or payload.get('pageId'),
               show=event.get('showId') or payload.get('showId'), schedule=event.get('scheduleId'),
               simulation=event.get('isSimulation') if type(event.get('isSimulation')) is bool else None,
               result=event.get('result'), booking=details.get('bookingId'),
               quantity=quantity if type(quantity) is int and quantity > 0 else None,
               request=details.get('requestId') if kind == 'server_request' else None,
               start=at-duration if duration is not None and kind == 'server_request' else None,
               duration=duration, status=response.get('status') if isinstance(response, dict) else None,
               sequence=event.get('requestSequence'))
    row['error'] = event.get('errorCode')
    for key in ('user', 'attempt', 'page', 'show', 'schedule', 'booking', 'request', 'error'):
        if not isinstance(row[key], str) or not 0 < len(row[key]) <= 256:
            row[key] = None
    # 본문·헤더·토큰·좌석 정보는 복제하지 않는다.
    row['fingerprint'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return row


def evaluate(observations, policy, now, *, window=3600, grace=300):
    """행동 이상 관측만 판정한다. false는 적용된 규칙 범위에서의 비해당이다."""
    unique = {}
    for row in observations:
        if now-window-grace <= row['time'] <= now:
            if row['id'] in unique and unique[row['id']]['fingerprint'] != row['fingerprint']:
                raise ValueError('Conflicting anomaly observation')
            unique[row['id']] = row
    rows = sorted(unique.values(), key=lambda row: (row['time'], row['id']))
    attempts = defaultdict(set)
    for row in rows:
        if row['attempt'] and row['user'] and row['show'] and row['schedule']:
            attempts[row['attempt']].add((row['user'], row['show'], row['schedule'], row['simulation']))
    def enriched(row):
        anchors = attempts[row['attempt']]
        if len(anchors) != 1:
            return row
        user, show, schedule, simulation = next(iter(anchors))
        if row['user'] != user or row['show'] not in (None, show) or row['schedule'] not in (None, schedule):
            return row
        return dict(row, show=show, schedule=schedule,
                    simulation=simulation if row['simulation'] is None else row['simulation'])
    rows = [enriched(row) for row in rows]
    by_scope = defaultdict(list)
    for row in rows:
        by_scope[(row['user'], row['show'], row['schedule'], row['simulation'])].append(row)
    findings, eligible = {}, defaultdict(set)
    version = policy.version()

    def hit(rule, current, evidence, observed):
        identity = hashlib.sha256(f'{version}:{rule}:{current["id"]}'.encode()).hexdigest()
        findings[identity] = dict(_id=identity, eventId=current['id'], isAnomaly=True,
            rule=rule, label=LABELS[rule], phase='pre' if rule in ('overlap', 'retry', 'pages') else 'post',
            occurredAt=current['time'], user=current['user'], attempt=current['attempt'],
            show=current['show'], schedule=current['schedule'], simulation=current['simulation'],
            ruleVersion=version, threshold=asdict(policy)[rule], observed=observed,
            evidence=[dict(eventId=r['id'], at=r['time'], kind=r['kind']) for r in evidence])

    for (user, show, schedule, simulation), group in by_scope.items():
        if not user or not show or not schedule:
            continue  # 공연·회차가 불명확한 자료는 결합하지 않는다.
        requests, confirms, bookings, starts = [], [], {}, {}
        for row in group:
            if row['kind'] in ('queue_enter', 'queue_register') and row['attempt']:
                starts.setdefault(row['attempt'], row)
            if row['kind'] == 'server_request' and row['attempt'] and row['request'] and row['start'] is not None:
                if any(r['request'] == row['request'] for r in requests):
                    previous = next(r for r in requests if r['request'] == row['request'])
                    if (previous['start'], previous['status']) != (row['start'], row['status']):
                        raise ValueError('Conflicting server request identity')
                    continue
                if policy.overlap:
                    eligible[row['id']].add('overlap')
                    overlaps = [r for r in requests if r['attempt'] == row['attempt'] and
                                max(r['start'], row['start']) < min(r['time'], row['time'])]
                    if overlaps:
                        hit('overlap', row, [overlaps[-1], row], min(overlaps[-1]['time'], row['time'])-max(overlaps[-1]['start'], row['start']))
                requests.append(row)
            if row['kind'] == 'booking_confirm_request' and row['attempt']:
                for rule in ('retry', 'pages'):
                    threshold = getattr(policy, rule)
                    if threshold is None or (rule == 'pages' and not row['page']):
                        continue
                    eligible[row['id']].add(rule)
                    matches = [r for r in confirms if row['time']-r['time'] <= threshold and
                               (r['attempt'] == row['attempt'] if rule == 'retry' else r['page'] and r['page'] != row['page'])]
                    if rule == 'retry':
                        matches = [r for r in matches if not any(
                            failure['kind'] == 'booking_confirm_fail' and failure['attempt'] == row['attempt'] and
                            failure['error'] in ('SEAT_SOLD', 'INSUFFICIENT_STOCK') and r['time'] <= failure['time'] <= row['time']
                            for failure in group)]
                    if matches:
                        hit(rule, row, [matches[-1], row], row['time']-matches[-1]['time'])
                confirms.append(row)
            if row['kind'] != 'booking_confirm_success' or row['result'] != 'success' or not row['booking'] or not row['quantity']:
                continue
            if row['booking'] in bookings:
                previous = bookings[row['booking']]
                if previous['quantity'] != row['quantity'] or previous['attempt'] != row['attempt']:
                    raise ValueError('Conflicting purchase result')
                continue
            bookings[row['booking']] = row
            if policy.fast is not None and row['attempt'] in starts:
                eligible[row['id']].add('fast')
                start = starts[row['attempt']]
                if row['time']-start['time'] < policy.fast:
                    hit('fast', row, [start, row], row['time']-start['time'])
            if policy.purchases is not None:
                eligible[row['id']].add('purchases')
                recent = [r for r in bookings.values() if row['time']-r['time'] <= policy.purchases]
                if len(recent) > 1:
                    hit('purchases', row, recent, len(recent))
            # 한도는 이 관측 시간창의 구매 수량 기준이다. 평생·누적 한도로 확대하지 않는다.
            if policy.limit is not None:
                eligible[row['id']].add('limit')
                quantity = sum(r['quantity'] for r in bookings.values() if row['time']-r['time'] <= 3600)
                if quantity > policy.limit:
                    hit('limit', row, [r for r in bookings.values() if row['time']-r['time'] <= 3600], quantity)
    evaluated = []
    bad = {f['eventId'] for f in findings.values()}
    for row in rows:
        pending = row['time'] > now-grace
        state = 'evaluated' if row['id'] in bad else 'pending' if pending else 'evaluated' if eligible[row['id']] else 'unavailable'
        evaluated.append(dict(eventId=row['id'], isAnomaly=None if state != 'evaluated' else row['id'] in bad,
                              evaluationStatus=state, evaluatedRules=sorted(eligible[row['id']]), ruleVersion=version))
    return rows, list(findings.values()), evaluated
