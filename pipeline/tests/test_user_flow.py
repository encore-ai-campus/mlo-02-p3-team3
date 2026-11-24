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


def test_publish_expiry_and_restart_after_failed_raw_verification(tmp_path):
    p, clock = processor(tmp_path)
    feed(p,[event('enter', NOW-10, 'queue_enter')])
    first = p.target.flow_public.find_one({'_id':'all'})['version']
    with patch('user_flow.runtime.time.sleep'):
        with pytest.raises(RuntimeError):
            p.batch(Frame([SimpleNamespace(topic='events',partition=0,offset=100,value=event('later',NOW))]),1)
    assert p.target.flow_public.find_one({'_id':'all'})['version'] == first
    assert p.target.flow_health.find_one()['status'] == 'blocked'
    p.tick()
    assert p.target.flow_public.find_one({'_id':'all'})['version'] == first
    p = Processor(p.config,p.raw,p.target,clock=lambda:clock[0])
    feed(p,[event('later', NOW)],batch=1)
    clock[0] += 40; p.tick()
    current, series = read_public(p.target)
    assert current['metrics']['current'] == 0
    assert len(series['presence']) == len(series['booking']) == 720
    assert next(v for v in series['booking'] if v['at']==iso(NOW+20))['observed'] is False


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


def test_filtered_reference_mismatch_blocks_every_scope(tmp_path):
    p, _ = processor(tmp_path)
    feed(p,[event('enter',NOW-5,'queue_enter')])
    before = list(p.target.flow_public.find())
    reference = State(str(p.reference_path))
    row = reference.observations()[0]
    row['simulation'] = False
    reference.db.events.update_one({'_id': reference._key(row['id'])}, {'$set': {'payload': row}})
    reference.close()
    with pytest.raises(VerificationPending): p.tick()
    assert list(p.target.flow_public.find()) == before
    assert p.failed and p.target.flow_health.find_one()['status']=='blocked'


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
