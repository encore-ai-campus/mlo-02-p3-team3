"""원본 대조 후 최소 관측 저장과 검증 버전 공개. 부분 실패는 새 공개를 보류한다."""
import hashlib
import json
from datetime import datetime, timezone

from bson import BSON

from .events import scope
from .metrics import (EVALUATION_WINDOW, GRACE, MAX_OBSERVATIONS,
                      Policy, calculate, findings, actions)


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc)


def begin(database):
    database.early_health.update_one({'_id': 'pipeline'}, {'$set': {'status': 'processing'}}, upsert=True)


def block(database):
    database.early_health.update_one({'_id': 'pipeline'}, {'$set': {'status': 'blocked'}}, upsert=True)


def observe(database, row, origin):
    if row is None:
        return
    prior = database.early_observations.find_one({'_id': row['id']})
    if prior and prior['fingerprint'] != row['fingerprint']:
        raise ValueError('Conflicting durable observation')
    database.early_observations.update_one({'_id': row['id'], 'fingerprint': row['fingerprint']},
        {'$setOnInsert': dict(row, _id=row['id'], origin=origin,
                             expiresAt=utc(row['time']+EVALUATION_WINDOW+GRACE+86400))}, upsert=True)


def validate(document):
    if document['contractVersion'] != 1 or document['policy']['mode'] != 'observe':
        raise ValueError('Invalid observation publication')
    if document['windowEnd']-document['windowStart'] != 3600:
        raise ValueError('Invalid observation window')
    if document['signalCount'] is not None and document['signalCount'] < len(document['detections']):
        raise ValueError('Detection count mismatch')
    if document['actionCount'] is not None and document['actionCount'] != sum(document['actionCounts'].values()):
        raise ValueError('Action count mismatch')
    if any(h['ruleVersion'] != document['ruleVersion'] or h['mode'] != 'observe' for h in document['detections']):
        raise ValueError('Rule version mismatch')
    if document['signalCount'] is not None and document['signalCount'] != sum(p['detections'] or 0 for p in document['series']):
        raise ValueError('Detection series mismatch')
    if document['actionCount'] is not None and document['actionCount'] != sum(p['actions'] or 0 for p in document['series']):
        raise ValueError('Action series mismatch')
    for row in document['stages']:
        if row['events'] is not None and row['events'] != sum(row['outcomes'].values()):
            raise ValueError('Stage outcome mismatch')
    for metric in document.get('stageDetections',{}).values():
        if metric['count'] is not None and (metric['eventCount'] is None or not 0 <= metric['count'] <= metric['eventCount']):
            raise ValueError('Stage detection unit mismatch')
        if metric['eventCount'] is not None and metric['eventCount'] < len(metric['cases']):
            raise ValueError('Stage detection list mismatch')
        if len(metric['cases']) > 200:
            raise ValueError('Stage detection list capacity exceeded')
    if len(BSON.encode(document)) > 8*1024*1024:
        raise ValueError('Observation publication capacity exceeded')


def refresh(database, now, policy=None, *, allow_recovery=False):
    policy = policy or Policy.load()
    health = database.early_health.find_one({'_id': 'pipeline'})
    if health and health['status'] in ('blocked', 'processing') and not allow_recovery:
        return
    begin(database)
    try:
        database.early_observations.create_index([('time', 1), ('_id', 1)])
        database.early_observations.create_index('expiresAt', expireAfterSeconds=0)
        database.early_versions.create_index('expiresAt', expireAfterSeconds=0)
        rows = list(database.early_observations.find({'time': {'$gte': now-EVALUATION_WINDOW-GRACE, '$lte': now}})
                    .sort([('time', 1), ('_id', 1)]).limit(MAX_OBSERVATIONS+1).max_time_ms(3000))
        if len(rows) > MAX_OBSERVATIONS:
            raise ValueError('Observation capacity exceeded')
        # 실제·시뮬레이션·미확인 범위를 공개 저장 단계부터 분리한다. 혼합 all은 게시하지 않는다.
        scopes = {scope(row) for row in rows} | {
            json.dumps([simulation, None, None], separators=(',', ':')) for simulation in (False, True, None)}
        if len(scopes) > 100:
            raise ValueError('Observation scope capacity exceeded')
        documents = {selected: calculate(rows, now, policy, selected) for selected in sorted(scopes)}
        for document in documents.values():
            validate(document)
        # 같은 불변 입력을 역순으로 다시 계산해 순서·중복 의존을 공개 전에 검출한다.
        if any(document != calculate(list(reversed(rows)), now, policy, selected)
               for selected, document in documents.items()):
            raise ValueError('Observation recalculation mismatch')
        active_findings = findings(rows, policy)
        active_ids = []
        for hit in active_findings:
            if hit['at'] < now-EVALUATION_WINDOW:
                continue
            active_ids.append(hit['id'])
            database.early_detections.replace_one({'_id': hit['id']}, dict(hit, _id=hit['id'], status='active'), upsert=True)
        database.early_detections.update_many({'ruleVersion': policy.version(),
            'at': {'$gte': now-EVALUATION_WINDOW, '$lte': now}, '_id': {'$nin': active_ids}},
            {'$set': {'status': 'retracted'}})
        from .server_policies import decisions
        for decision in decisions(rows):
            if decision['kind'] == 'prevention_detected' and decision['time'] >= now-EVALUATION_WINDOW:
                identity = 'server:' + decision['policy']['restrictionId']
                database.early_detections.replace_one({'_id': identity},
                    dict(decision, _id=identity, status='server_decision'), upsert=True)
        for action in actions(rows):
            if action['at'] >= now-EVALUATION_WINDOW:
                database.early_actions.replace_one({'_id': action['id']}, dict(action, _id=action['id']), upsert=True)
        payload = dict(contractVersion=1, asOf=now, ruleVersion=policy.version(),
                       scopes=documents, inputCount=len(rows),
                       ranges=sorted({json.dumps(r['origin'], sort_keys=True) for r in rows}))
        version = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        wrapper = dict(_id=version, version=version, data=payload, publishedAt=utc(now))
        if len(BSON.encode(wrapper)) > 8*1024*1024:
            raise ValueError('Observation version capacity exceeded')
        database.early_versions.update_one({'_id': version}, {'$setOnInsert': wrapper,
                                                            '$unset': {'expiresAt': ''}}, upsert=True)
        # 모든 범위·카드·시계열이 하나의 불변 버전으로 준비된 뒤 포인터를 교체한다.
        database.early_public.replace_one({'_id': 'pipeline'},
            {'_id': 'pipeline', 'version': version, 'asOf': now}, upsert=True)
        database.early_health.replace_one({'_id': 'pipeline'},
            {'_id': 'pipeline', 'status': 'ready', 'version': version, 'asOf': now}, upsert=True)
        database.early_versions.update_many({'_id': {'$ne': version}, 'expiresAt': {'$exists': False}},
                                           {'$set': {'expiresAt': utc(now+300)}})
    except Exception:
        block(database)
        raise
