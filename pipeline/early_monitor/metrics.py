"""시간창·단위가 명시된 관찰 전용 계산. 실제 업무 상태를 변경하지 않는다."""
import hashlib
import json
import math
import os
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from statistics import median

from .events import scope
from .stage_detections import calculate as stage_detections

DISPLAY_WINDOW = 3600
EVALUATION_WINDOW = 7200
GRACE = 300
MAX_OBSERVATIONS = 25000
STAGES = (
    ('login', '로그인', 'login', ('login_success', 'login_fail')),
    ('start', '예매 시작', 'start', ('queue_enter', 'booking_not_open')),
    ('queue', '대기열', None, ('queue_enter', 'queue_register', 'queue_admitted', 'queue_cancel', 'connection_timeout')),
    ('captcha', '보안문자', 'verify', ('captcha_success', 'captcha_wrong', 'captcha_limit_reached', 'captcha_refresh')),
    ('confirm', '예매 확정', 'confirm', ('booking_confirm_request', 'booking_confirm_success',
                                     'booking_confirm_fail', 'booking_confirm_rejected', 'booking_confirm_replayed')),
)
ACTION_RULES = {
    'NOT_OPEN': ('오픈 전 시작 거절', 'opening-existing', None),
    'ACTIVE_ATTEMPT': ('기존 예매 진행 보호', 'ownership-existing', None),
    'AUTH_REQUIRED': ('인증 필요 요청 거절', 'authentication-existing', None),
    'EXPIRED': ('진행 권한 만료', 'timeout-existing', None),
    'CAPTCHA_LIMIT': ('시도 취소', 'captcha-limit-v1', 5),
    'LOGIN_LOCKED': ('로그인 제한', 'login-limit-existing', None),
    'INVALID_STEP': ('단계 접근 거절', 'step-existing', None),
    'NOT_VERIFIED': ('구매 요청 거절', 'step-existing', None),
    'DUPLICATE_PAGE': ('중복 페이지 거절', 'ownership-existing', None),
    'STALE_ATTEMPT': ('이전 시도 거절', 'ownership-existing', None),
    'STALE_REQUEST': ('이전 요청 거절', 'sequence-existing', None),
    'SELECTION_LIMIT': ('수량·좌석 요청 거절', 'selection-existing', 4),
}


@dataclass(frozen=True)
class Policy:
    enabled: bool = True
    seconds: float = 3.0
    count: int = 10
    mode: str = 'observe'
    rule: str = 'confirm-receipt-count-v1'

    @classmethod
    def load(cls):
        enabled = os.environ.get('EARLY_MONITOR_ENABLED', '1')
        seconds = float(os.environ.get('EARLY_MONITOR_SECONDS', '3'))
        count = int(os.environ.get('EARLY_MONITOR_COUNT', '10'))
        if enabled not in ('0', '1') or not math.isfinite(seconds) or not 0 < seconds <= 3600 or not 2 <= count <= MAX_OBSERVATIONS:
            raise ValueError('Invalid early observation policy')
        return cls(enabled == '1', seconds, count)

    def version(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:20]


def unique(rows):
    records = {}
    for row in rows:
        previous = records.get(row['id'])
        if previous and previous['fingerprint'] != row['fingerprint']:
            raise ValueError('Conflicting observation identity')
        records[row['id']] = row
    return sorted(records.values(), key=lambda r: (r['time'], r['id']))


def request_unique(rows):
    result = {}
    for row in rows:
        # 새 서버 요청은 새 ID, 같은 이벤트 재전달은 같은 ID다.
        key = row['request'] or row['id']
        if key in result and result[key]['fingerprint'] != row['fingerprint']:
            raise ValueError('Conflicting request receipt')
        result[key] = row
    return sorted(result.values(), key=lambda r: (r['time'], r['id']))


def findings(rows, policy):
    if not policy.enabled:
        return []
    groups = defaultdict(list)
    for row in rows:
        if row['kind'] == 'ticketing_action_received' and row['action'] == 'confirm' and all(
                row[key] for key in ('request', 'user', 'attempt', 'show', 'schedule')) and row['simulation'] is not None:
            groups[(row['user'], row['attempt'], scope(row))].append(row)
    hits = []
    for group in groups.values():
        window = deque()
        for row in request_unique(group):
            while window and window[0]['time'] < row['time'] - policy.seconds:
                window.popleft()
            window.append(row)
            if len(window) >= policy.count:
                # 기준을 만족한 요청마다 1개. 에피소드 수나 계정 수와 합산하지 않는다.
                identity = hashlib.sha256(f'{policy.version()}:{row["id"]}'.encode()).hexdigest()
                hits.append(dict(id=identity, eventId=row['id'], at=row['time'], user=row['user'],
                                 attempt=row['attempt'], scope=scope(row), observed=len(window),
                                 threshold=policy.count, seconds=policy.seconds, ruleVersion=policy.version(),
                                 mode='observe', evidence=[r['id'] for r in list(window)[-policy.count:]]))
    return sorted(hits, key=lambda h: (h['at'], h['id']))


def actions(rows):
    result = {}
    from .server_policies import decisions
    server_decisions = decisions(rows)
    releases = {row['policy']['restrictionId']:row for row in server_decisions if row['kind']=='prevention_released'}
    for row in server_decisions:
        if row['kind'] != 'prevention_applied':
            continue
        data = row['policy']
        release = releases.get(data['restrictionId'])
        identity = 'restriction:' + data['restrictionId']
        result[identity] = dict(id=identity, request=row['request'], eventId=row['id'],
            at=row['time'], user=row['user'], attempt=row['attempt'], scope=scope(row),
            reason=data['ruleId'], label='5분 일시 제한 · ' + data['ruleId'],
            ruleVersion=data['ruleVersion'], threshold=data['threshold'], observed=data['observed'],
            appliedAt=data['appliedAt'], expiresAt=data['expiresAt'], restrictionId=data['restrictionId'],
            releasedAt=release['policy']['releasedAt'] if release else None,
            releaseEventId=release['id'] if release else None,
            releaseStatus=release['policy']['releaseReason'] if release else 'expiry_known',
            status='observed_released' if release else 'observed_applied')
    for row in rows:
        code = row['error']
        if row['kind'] == 'booking_confirm_replayed':
            code = 'PURCHASE_REPLAYED'
            label, version, threshold = '기존 구매 재응답', 'idempotency-existing', None
        elif code in ACTION_RULES and row['kind'] in ('captcha_limit_reached', 'login_fail',
                'booking_access_rejected', 'booking_confirm_rejected', 'booking_confirm_fail',
                'booking_not_open', 'connection_timeout'):
            label, version, threshold = ACTION_RULES[code]
        else:
            continue
        if not row['request']:
            # 업무 사건 자체는 관측했지만 같은 조치의 다른 이벤트와 안전하게 결합할 수 없다.
            continue
        identity = hashlib.sha256(f'{row["request"]}:{code}'.encode()).hexdigest()
        result.setdefault(identity, dict(id=identity, request=row['request'], eventId=row['id'],
            at=row['time'], user=row['user'], attempt=row['attempt'], scope=scope(row),
            reason=code, label=label, ruleVersion=version, threshold=threshold,
            observed=row['wrong'] if code == 'CAPTCHA_LIMIT' else None,
            expiresAt=None, releaseStatus='unobserved', status='observed_applied'))
        if code == 'CAPTCHA_LIMIT' and row['wrong'] is not None:
            result[identity]['observed'] = row['wrong']
    return sorted(result.values(), key=lambda r: (r['at'], r['id']))


def durations(rows, now):
    groups = defaultdict(list)
    for row in rows:
        if all(row[k] for k in ('user', 'attempt', 'show', 'schedule')) and row['simulation'] is not None:
            groups[(row['user'], row['attempt'], scope(row))].append(row)
    specs = [('queue', '대기열 진입 → 입장 허용', 'queue_enter', 'queue_admitted'),
             ('captcha', '입장 허용 → 보안문자 성공', 'queue_admitted', 'captcha_success'),
             ('selection', '보안문자 성공 → 첫 확정 요청 수신', 'captcha_success', 'ticketing_action_received'),
             ('purchase', '확정 요청 수신 → 구매 성공', 'ticketing_action_received', 'booking_confirm_success')]
    output = []
    for key, label, start_kind, end_kind in specs:
        samples, unavailable = [], 0
        for group in groups.values():
            ends = [r for r in group if r['kind'] == end_kind and
                    (key != 'selection' or r['action']=='confirm')]
            if key == 'selection':
                ends = ends[:1]
            ends = [r for r in ends if now-DISPLAY_WINDOW <= r['time'] <= now]
            for end in ends:
                candidates = [r for r in group if r['kind'] == start_kind and r['time'] <= end['time']]
                if key == 'purchase':
                    candidates = [r for r in candidates if r['action']=='confirm' and end['request'] and r['request'] == end['request']]
                # 여러 번 성공·입장이 관측된 경우 임의 선택하지 않는다.
                if key == 'selection':
                    candidates = [r for r in candidates if r['kind'] == 'captcha_success']
                if len(candidates) != 1:
                    unavailable += 1
                else:
                    samples.append(end['time']-candidates[0]['time'])
        eligible = {r['id'] for group in groups.values() for r in group}
        unavailable += sum(r['kind'] == end_kind and (key != 'selection' or r['action']=='confirm') and now-DISPLAY_WINDOW <= r['time'] <= now and r['id'] not in eligible for r in rows)
        values = sorted(samples)
        output.append(dict(key=key, label=label, samples=len(values), unavailable=unavailable,
                           mean=sum(values)/len(values) if values else None,
                           median=median(values) if values else None,
                           p95=values[math.ceil(.95*len(values))-1] if values else None))
    return output


def calculate(rows, now, policy, selected='all'):
    history = unique(r for r in rows if now-EVALUATION_WINDOW-GRACE <= r['time'] <= now)
    if selected != 'all':
        simulation, show, schedule = json.loads(selected)
        history = [r for r in history if r['simulation'] == simulation
                   and (show is None or r['show'] == show) and (schedule is None or r['schedule'] == schedule)]
    visible = [r for r in history if r['time'] >= now-DISPLAY_WINDOW]
    hits = [h for h in findings(history, policy) if h['at'] >= now-DISPLAY_WINDOW]
    applied = actions(visible)
    signal_available = policy.enabled and any(r['kind']=='ticketing_action_received' and r['action']=='confirm'
        and r['request'] and r['user'] and r['attempt'] and r['show'] and r['schedule']
        and r['simulation'] is not None for r in visible)
    action_available = any(r['request'] for r in visible)
    stages = []
    for key, label, action, kinds in STAGES:
        events = [r for r in visible if r['kind'] in kinds]
        if key == 'queue':
            events = [r for r in events if r['kind'] not in ('queue_cancel','connection_timeout') or r['stage']=='queue']
        access = [r for r in visible if r['kind'] == 'booking_access_rejected' and
                  r['action'] in ({'start'} if key == 'start' else
                                 {'heartbeat','page_queue'} if key == 'queue' else
                                 {'verify','page_captcha'} if key == 'captcha' else set())]
        events += access
        received = request_unique([r for r in visible if r['kind'] == 'ticketing_action_received' and r['action'] == action]) if action else []
        related = events + received
        outcomes = {kind: sum(r['kind'] == kind for r in events) if related else None for kind in kinds}
        if key in ('start','queue','captcha'):
            outcomes['booking_access_rejected'] = len(access) if related else None
        if key == 'login':
            outcomes.pop('login_fail')
            outcomes['login_failed'] = sum(r['kind']=='login_fail' and r['result']=='failure' for r in events) if related else None
            outcomes['login_locked'] = sum(r['kind']=='login_fail' and r['result']=='rejected' and r['error']=='LOGIN_LOCKED' for r in events) if related else None
        stages.append(dict(key=key, label=label, requests=len(received) if received else None,
                           events=len(events) if related else None,
                           attempts=len({r['attempt'] for r in related if r['attempt']}) if any(r['attempt'] for r in related) else None,
                           accounts=len({r['user'] for r in related if r['user']}) if any(r['user'] for r in related) else None,
                           outcomes=outcomes))
    context = {h['eventId']: [dict(eventId=r['id'], kind=r['kind'], result=r['result'], error=r['error'])
                for r in history if r['request'] == next(x['request'] for x in history if x['id'] == h['eventId'])][:20]
               for h in hits[-200:]}
    series = []
    for at in range(int((now-DISPLAY_WINDOW)//60)*60, int(now//60)*60+1, 60):
        observed = any(at <= r['time'] < at+60 for r in visible)
        series.append(dict(at=at, observed=observed,
                           detections=sum(at <= h['at'] < at+60 for h in hits) if observed and signal_available else None,
                           actions=sum(at <= r['at'] < at+60 for r in applied) if observed and action_available else None))
    from .server_policies import metrics as server_metrics
    stage_metrics = stage_detections(visible,hits,policy,applied)
    server = server_metrics(history, now)
    # Older producers have no server policy evidence. Never turn that into a zero.
    if server['login_repeated_failure']['count'] is None:
        server.pop('login_limit')
    stage_metrics.update(server)
    return dict(contractVersion=1, scope=selected, asOf=now, windowStart=now-DISPLAY_WINDOW,
                windowEnd=now, evaluationSeconds=EVALUATION_WINDOW, policy=asdict(policy), ruleVersion=policy.version(),
                status='observed' if visible else 'unobserved', stages=stages,
                stageDetections=stage_metrics,
                signalCount=len(hits) if signal_available else None,
                signalAttempts=len({h['attempt'] for h in hits}) if signal_available else None,
                detections=hits[-200:], contexts=context,
                actionCount=len(applied) if action_available else None,
                actionAttempts=len({r['attempt'] for r in applied if r['attempt']}) if action_available and any(r['attempt'] for r in visible) else None,
                actionSubjects=len({r['user'] for r in applied if r['user']}) if action_available and any(r['user'] for r in visible) else None,
                actionCounts={label: sum(r['label']==label for r in applied) for label in sorted({r['label'] for r in applied})},
                actions=applied[-200:], durations=durations(history, now), series=series,
                truncated=len(hits)>200 or len(applied)>200,
                unlinkedActions=sum(r['kind'] in ('captcha_limit_reached', 'booking_confirm_replayed') or r['error'] in ACTION_RULES for r in visible if not r['request']),
                unknownContext=sum(not all(r[k] for k in ('user','attempt','show','schedule')) or r['simulation'] is None for r in visible),
                unknownQueueStage=sum(r['kind'] in ('queue_cancel','connection_timeout') and r['stage'] is None for r in visible),
                coverage='수신·원본 대조 완료 자료만 집계 · 수집 완전성 미보장 · 현재 대기 인원 아님',
                fastPolicy='not_added')
