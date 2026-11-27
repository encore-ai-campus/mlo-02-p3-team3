"""
[홍석환] 사용자 흐름 계산·원본 검증·공개 복구 검사
수작업 기대값과 격리 DB로 한 시간 경계·중복·실패·무이벤트 갱신을 검증한다.
"""
import base64
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import mongomock
import pytest

from configuration.user_flow import FlowConfig
from user_flow.events import decode, iso, project
from user_flow.metrics import calculate
from user_flow.runtime import Processor
from user_flow.state import State
from user_flow.store import publish, read_public
from user_flow.verification import VerificationPending, verified_records

NOW = 1800000000


def event(id_, at, kind='page_enter', stage='queue', actor='tab1', attempt='try1', **extra):
    value = dict(schemaVersion=1, eventId=id_, eventType=kind, occurredAt=iso(at), receivedAt=iso(at),
                 details={'pageName': stage}, pageInstanceId=actor, pageViewId=actor+stage,
                 bookingAttemptId=attempt, source='server', result='success', isSimulation=True,
                 showId='show', scheduleId='schedule')
    value.update(extra)
    return json.dumps(value).encode()


class Frame:
    def __init__(self, rows): self.rows = rows
    def toLocalIterator(self, **kwargs): return iter(self.rows)


def processor(tmp_path):
    db = mongomock.MongoClient(tz_aware=True)
    config = FlowConfig('unused', 'events', 'spark', 'unused', 'unused', 'isolated', str(tmp_path))
    config.raw_wait_seconds = 0
    clock = [NOW]
    p = Processor(config, db.raw, db.metrics, clock=lambda: clock[0])
    return p, clock


def feed(p, values, batch=0):
    rows = [SimpleNamespace(topic='events', partition=0, offset=batch*100+i, value=v) for i,v in enumerate(values)]
    for row in rows:
        p.raw.booking_events.insert_one(dict(source='isolated', topic=row.topic, partition=row.partition,
                                             offset=row.offset, value=row.value))
    p.batch(Frame(rows), batch)
    return rows


def test_hand_calculated_dwell_retry_success_and_shared_account():
    raw = [event('start', NOW-20, 'queue_enter'), event('dup-start', NOW-20, 'queue_register'),
           event('captcha', NOW-15, 'page_enter', 'captcha'),
           event('retry', NOW-12, 'page_enter', 'captcha', pageViewId='retry-view'),
           event('success', NOW-5, 'booking_confirm_success', 'complete'),
           event('second-tab', NOW-3, actor='tab2', attempt=None)]
    m = calculate([project(decode(v)) for v in raw], NOW)
    by_id = {n['id']: n for n in m['nodes']}
    assert m['booking'] == dict(numerator=1, denominator=1, ratio=1)
    assert m['current'] == sum(n['current'] for n in m['nodes']) == 2
    assert by_id['queue']['dwell'] == dict(meanSeconds=5, medianSeconds=5, samples=1)
    assert by_id['captcha']['dwell'] == dict(meanSeconds=5, medianSeconds=5, samples=2)
    assert next(e for e in m['edges'] if e['kind']=='retry')['moves'] == 1


def test_one_hour_boundary_and_late_success():
    rows = [project(decode(v)) for v in [event('old', NOW-3601, 'queue_enter', attempt='old'),
            event('edge', NOW-3600, 'queue_enter'), event('success', NOW-1, 'booking_confirm_success')]]
    m = calculate(rows, NOW)
    assert m['windowStart'] == iso(NOW-3600)
    assert m['booking'] == dict(numerator=1, denominator=1, ratio=1)


def test_timeout_is_not_completed_dwell():
    rows = [project(decode(event('enter', NOW-40))), project(decode(event('timeout', NOW-5, 'connection_timeout')))]
    m = calculate(rows, NOW)
    assert m['current'] == 0
    assert next(n for n in m['nodes'] if n['id']=='queue')['dwell']['samples'] == 0


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'different'])
def test_raw_position_failures(failure):
    db = mongomock.MongoClient().raw
    row = SimpleNamespace(topic='events', partition=0, offset=3, value=b'payload')
    doc = dict(source='s', topic='events', partition=0, offset=3, value=b'payload')
    if failure != 'missing': db.booking_events.insert_one(doc)
    if failure == 'duplicate': db.http_events.insert_one(deepcopy(doc))
    if failure == 'different': row.value = b'changed'
    with pytest.raises(VerificationPending): verified_records(db, 's', [row])


def test_chunk_reassembly_and_conflict(tmp_path):
    raw = event('assembled', NOW)
    chunks = []
    for i, part in enumerate([raw[:50], raw[50:]]):
        chunks.append(event('chunk'+str(i), NOW, 'capture_chunk', schemaVersion=2,
            details=dict(recordId='assembled',count=2,index=i,totalBytes=len(raw),
                         sha256=hashlib.sha256(raw).hexdigest(),encoding='base64-json-utf8',
                         data=base64.b64encode(part).decode())))
    s = State(str(tmp_path/'state'))
    try:
        s.begin(0); s.feed(chunks[0],0,NOW,NOW-3600); s.finish(0,[])
        assert not s.ready()
        s.begin(1); s.feed(chunks[1],1,NOW,NOW-3600); s.finish(1,[])
        assert s.ready() and len(s.observations()) == 1
        s.feed(raw,1,NOW,NOW-3600)
        assert len(s.observations()) == 1
    finally: s.close()


def test_publication_does_not_wait_for_raw_original(tmp_path):
    # 실시간 경로는 원본 DB를 조회하지 않는다 · 원본 미적재여도 공개
    p, clock = processor(tmp_path)
    rows = [SimpleNamespace(topic='events', partition=0, offset=0, value=event('enter', NOW-10, 'queue_enter'))]
    p.batch(Frame(rows), 0)
    assert p.raw.booking_events.count_documents({}) == 0
    assert p.target.flow_public.find_one({'_id': 'all'})
    assert p.target.flow_health.find_one()['status'] == 'verified'
    clock[0] += 40; p.tick()
    current, _ = read_public(p.target)
    assert current['metrics']['current'] == 0


def test_partial_store_failure_keeps_public_pointer():
    db = mongomock.MongoClient().metrics
    m = calculate([], NOW)
    first = publish(db,m,[],NOW)
    m2 = calculate([], NOW+1)
    with patch.object(db.flow_series,'update_one',side_effect=RuntimeError('write failure')):
        with pytest.raises(RuntimeError): publish(db,m2,[],NOW+1)
    assert db.flow_public.find_one()['version'] == first
    publish(db,m2,[],NOW+1)
    publish(db,m,[],NOW)
    assert 'expiresAt' not in db.flow_versions.find_one({'_id':first})


def test_active_attempt_evidence_survives_pruning(tmp_path):
    s = State(str(tmp_path/'state'))
    try:
        s.feed(event('start',NOW-4000,'queue_enter'),0,NOW,NOW-5000)
        s.feed(event('heartbeat',NOW-1),0,NOW,NOW-5000)
        s.prune(NOW,3600,300,30)
        assert len(s.observations()) == 2
    finally: s.close()


def test_config_one_hour_and_no_secret_in_representation(monkeypatch):
    for key in ('FLOW_RAW_MONGO_URI','FLOW_TARGET_MONGO_URI','RAW_SOURCE_ID'):
        monkeypatch.setenv(key,'hidden-value')
    config = FlowConfig.load()
    assert config.retention == 3600
    assert 'hidden-value' not in repr(config)


def test_raw_uri_is_optional_and_group_is_separate(monkeypatch):
    for key in ('FLOW_RAW_MONGO_URI', 'SPARK_KAFKA_GROUP_ID'):
        monkeypatch.delenv(key, raising=False)
    for key in ('FLOW_TARGET_MONGO_URI', 'RAW_SOURCE_ID'):
        monkeypatch.setenv(key, 'hidden-value')
    config = FlowConfig.load()
    assert config.raw_uri == ''
    assert config.group == 'ticketguard-flow-v1'  # Spark 정제 그룹과 분리


@pytest.mark.parametrize('value,reason', [
    (event('old', NOW-3600-301, 'queue_enter'), 'late'),
    (event('ahead', NOW+31, 'queue_enter'), 'future'),
    (b'{not json', 'malformed'),
    (json.dumps({'schemaVersion': 9, 'eventId': 'x', 'eventType': 'page_enter'}).encode(), 'malformed'),
])
def test_out_of_range_or_malformed_event_is_skipped_and_counted(tmp_path, value, reason):
    p, _ = processor(tmp_path)
    rows = [SimpleNamespace(topic='events', partition=0, offset=0, value=value),
            SimpleNamespace(topic='events', partition=0, offset=1, value=event('enter', NOW-5, 'queue_enter'))]
    p.batch(Frame(rows), 0)  # 멈추지 않고 공개
    health = p.target.flow_health.find_one({'_id': 'pipeline'})
    assert health['status'] == 'verified' and not p.failed
    assert health['skipped'][reason] == 1
    assert sum(health['skipped'].values()) == 1
    state = p.state()
    assert [row['id'] for row in state.observations()] == ['enter']
    state.close()


def test_skipped_counts_accumulate_across_batches(tmp_path):
    p, _ = processor(tmp_path)
    for batch in (0, 1):
        rows = [SimpleNamespace(topic='events', partition=0, offset=batch, value=event('old'+str(batch), NOW-5000))]
        p.batch(Frame(rows), batch)
    assert p.target.flow_health.find_one({'_id': 'pipeline'})['skipped']['late'] == 2


def test_reprocessing_completed_batch_is_idempotent(tmp_path):
    p, _ = processor(tmp_path)
    rows = feed(p,[event('start',NOW-5,'queue_enter'),event('success',NOW-2,'booking_confirm_success')])
    p.batch(Frame(rows),0)
    current,_ = read_public(p.target)
    assert current['metrics']['booking']==dict(numerator=1,denominator=1,ratio=1)
    state = p.state()
    assert len(state.observations())==2
    state.close()


def test_unfinished_chunk_holds_publication(tmp_path):
    p, _ = processor(tmp_path)
    raw = event('assembled',NOW)
    chunk = event('chunk',NOW,'capture_chunk',details=dict(recordId='assembled',count=2,index=0,
        totalBytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),encoding='base64-json-utf8',
        data=base64.b64encode(raw[:30]).decode()))
    feed(p,[chunk])
    assert p.target.flow_public.count_documents({})==0
    assert p.target.flow_health.find_one()['status']=='waiting_chunks'


def test_late_order_and_cancel_backward_paths():
    raw = [event('cancel',NOW-1,'queue_cancel'),event('start',NOW-20,'queue_enter'),
           event('select',NOW-15,stage='select'),event('back',NOW-5,stage='queue')]
    m = calculate([project(decode(v)) for v in raw],NOW)
    assert m['current']==0 and m['booking']['denominator']==1
    assert any(e['kind']=='backward' and e['moves']==1 for e in m['edges'])
    assert any(e['kind']=='cancel' and e['moves']==1 for e in m['edges'])
