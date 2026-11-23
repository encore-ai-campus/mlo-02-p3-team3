"""기존 ingest의 내구성 있는 최소 관측과 추가 저장·원자적 화면 게시."""
import json
from dataclasses import asdict
from datetime import datetime, timezone

from .rules import LABELS, Policy, evaluate

WINDOW = 3600
EVALUATION_WINDOW = 2 * WINDOW
GRACE = 300
MAX_OBSERVATIONS = 25000


def date(value):
    return datetime.fromtimestamp(value, timezone.utc)


def observe(database, row):
    """중복 입력은 동일 관측만 허용한다. 실행 시각으로 만료 기한을 연장하지 않는다."""
    collection = database.anomaly_observations
    prior = collection.find_one({'_id': row['id']}, {'fingerprint': 1})
    if prior and prior['fingerprint'] != row['fingerprint']:
        raise ValueError('Conflicting anomaly observation')
    collection.update_one({'_id': row['id'], 'fingerprint': row['fingerprint']}, {'$setOnInsert': dict(row, _id=row['id'],
        expiresAt=date(row['time']+WINDOW+GRACE+86400))}, upsert=True)


def indexes(database):
    database.anomaly_observations.create_index([('time', 1), ('_id', 1)])
    database.anomaly_observations.create_index('expiresAt', expireAfterSeconds=0)
    database.anomaly_events.create_index([('occurredAt', 1), ('ruleVersion', 1)])


def scope(row):
    return json.dumps([row['simulation'], row['show'], row['schedule']], separators=(',', ':'))


def publication(rows, findings, decisions, policy, now, selected='all'):
    rows = [r for r in rows if now-WINDOW <= r['time'] <= now and (selected == 'all' or scope(r) == selected)]
    ids = {r['id'] for r in rows}
    findings = [f for f in findings if f['eventId'] in ids]
    by_id = {r['id']: r for r in rows}
    pre = [f for f in findings if f['phase'] == 'pre']
    recent_pre = sorted(pre, key=lambda f: (f['occurredAt'], f['_id']), reverse=True)[:200]
    post = [f for f in findings if f['phase'] == 'post']
    counts = {rule: None if asdict(policy)[rule] is None else sum(f['rule'] == rule for f in findings) for rule in LABELS}
    applicable = [d for d in decisions if d['eventId'] in ids]
    covered_rules = {rule for d in applicable if d['evaluationStatus'] == 'evaluated' for rule in d['evaluatedRules']}
    # 단위: 사전 규칙은 고유 시도, 다중 페이지·사후 규칙은 고유 계정.
    for rule in counts:
        if counts[rule] is not None:
            counts[rule] = (len({(f['attempt'] if rule in ('overlap', 'retry') else f['user']) for f in findings if f['rule'] == rule})
                            if rule in covered_rules else None)
    purchases = {}
    for row in rows:
        if row['kind'] == 'booking_confirm_success' and row['result'] == 'success' and row['booking'] and row['quantity']:
            purchases.setdefault(row['booking'], row)
    accounts = []
    for user in sorted({r['user'] for r in purchases.values() if r['user']}):
        bought = [r for r in purchases.values() if r['user'] == user]
        signals = [f for f in post if f['user'] == user]
        accounts.append(dict(user=user, count=len(bought), quantity=sum(r['quantity'] for r in bought),
            signals=sorted({f['label'] for f in signals}), bookings=[dict(id=r['booking'], quantity=r['quantity'],
            show=r['show'], schedule=r['schedule'], at=r['time']) for r in bought],
            evidence=[dict(at=r['time'], kind=r['kind'], eventId=r['id']) for r in rows if r['user'] == user][:100],
            durations=[f['observed'] for f in signals if f['rule'] == 'fast']))
    # 차트의 0은 수집 완전성이 확인되지 않아 생성하지 않는다.
    series = []
    observed_buckets = {rule: {int(by_id[d['eventId']]['time']//60)*60 for d in applicable
        if d['evaluationStatus'] == 'evaluated' and rule in d['evaluatedRules']} for rule in ('overlap','retry')}
    for at in range(int((now-WINDOW)//60)*60, int(now//60)*60+1, 60):
        counts_at = {rule: sum(f['rule'] == rule and at <= f['occurredAt'] < at+60 for f in pre)
                     if at in observed_buckets[rule] else None for rule in ('overlap','retry')}
        series.append(dict(at=date(at).isoformat(), **counts_at,
                           observed=any(value is not None for value in counts_at.values())))
    return dict(contractVersion=1, scope=selected, asOf=date(now).isoformat(), windowStart=date(now-WINDOW).isoformat(),
        windowEnd=date(now).isoformat(), ruleVersion=policy.version(), policy=asdict(policy),
        status='observed' if rows else 'unobserved', counts=counts,
        attempts=len({f['attempt'] for f in pre if f['attempt']}) if covered_rules.intersection(('overlap','retry','pages')) else None,
        subjects=len(accounts) if covered_rules.intersection(('fast','purchases','limit')) else None,
        evaluated=sum(d['evaluationStatus'] == 'evaluated' for d in applicable),
        pending=sum(d['evaluationStatus'] == 'pending' for d in applicable),
        unavailable=sum(d['evaluationStatus'] == 'unavailable' for d in applicable),
        detections=recent_pre,
        timelines={r['id']: [dict(at=e['time'], kind=e['kind'], eventId=e['id']) for e in rows
                   if e['attempt'] == r['attempt'] and e['user'] == r['user'] and scope(e) == scope(r)][:100]
                   for r in [by_id[f['eventId']] for f in recent_pre]},
        accounts=accounts[:200], series=series,
        truncated=len(pre)>200 or len(accounts)>200,
        coverage='수신한 서버 관측 범위 · 수집 완전성 미보장')


def refresh(database, policy=None, now=None):
    policy = policy or Policy.load()
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    database.anomaly_health.replace_one({'_id': 'ingest'}, {'_id': 'ingest', 'status': 'processing', 'asOf': date(now)}, upsert=True)
    try:
        rows = list(database.anomaly_observations.find({'time': {'$gte': now-EVALUATION_WINDOW-GRACE, '$lte': now}})
                    .sort([('time', 1), ('_id', 1)]).limit(MAX_OBSERVATIONS+1).max_time_ms(3000))
        if len(rows) > MAX_OBSERVATIONS:
            raise ValueError('Anomaly observation capacity exceeded')
        rows, findings, decisions = evaluate(rows, policy, now, window=EVALUATION_WINDOW)
        # 유예 구간은 비교 근거로만 사용하고 판정 저장 범위는 2시간으로 맞춘다.
        cutoff = now - EVALUATION_WINDOW
        findings = [finding for finding in findings if finding['occurredAt'] >= cutoff]
        for finding in findings:
            database.anomaly_events.replace_one({'_id': finding['_id']}, finding, upsert=True)
        # 지연된 예외 근거로 철회된 신호는 이력을 보존하며 Boolean만 정정한다.
        database.anomaly_events.update_many({'ruleVersion': policy.version(),
            'occurredAt': {'$gte': cutoff, '$lte': now}, '_id': {'$nin': [f['_id'] for f in findings]}},
            {'$set': {'isAnomaly': False, 'evaluationStatus': 'retracted'}})
        # 이상 저장이 먼저 완료되어야 정상 문서 판정과 화면 게시를 진행한다.
        current_ids = {r['id'] for r in rows if r['time'] >= cutoff}
        for decision in decisions:
            if decision['eventId'] in current_ids:
                database.clean_events.update_one({'_id': decision['eventId']}, {'$set': decision})
        scopes = {'all'} | {scope(r) for r in rows}
        if len(scopes) > 101:
            raise ValueError('Anomaly scope capacity exceeded')
        for selected in sorted(scopes):
            document = publication(rows, findings, decisions, policy, now, selected)
            if len(json.dumps(document).encode()) > 8*1024*1024:
                raise ValueError('Anomaly publication capacity exceeded')
            database.anomaly_public.replace_one({'_id': selected}, dict(document, _id=selected), upsert=True)
        database.anomaly_health.replace_one({'_id': 'ingest'}, {'_id': 'ingest', 'status': 'ready',
            'asOf': date(now), 'ruleVersion': policy.version()}, upsert=True)
    except Exception:
        database.anomaly_health.replace_one({'_id': 'ingest'}, {'_id': 'ingest', 'status': 'blocked', 'asOf': date(now)}, upsert=True)
        raise
