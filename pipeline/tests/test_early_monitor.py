"""조기 관측 시간창·근거·공개 실패·원본 경계 회귀 검사."""
from copy import deepcopy
from types import SimpleNamespace

import mongomock
import pytest

from early_monitor.events import project
from early_monitor.metrics import Policy, calculate, findings, unique
from early_monitor.store import observe, refresh, validate
from user_flow.events import iso

NOW = 2000000000  # mongomock의 실제 시계 TTL 삭제와 고정 시험 시계를 분리한다.
REAL = '[false,null,null]'


def event(identity, at=NOW-10, kind='ticketing_action_received', **changes):
    result = dict(schemaVersion=1, eventId=identity, eventType=kind, occurredAt=iso(at),
        receivedAt=iso(at), source='server', userId='u', bookingAttemptId='a', showId='s',
        scheduleId='t', isSimulation=False, result='success', errorCode=None,
        details=dict(action='confirm', contextKnown=True, observationRequestId='req-'+identity))
    result.update(changes)
    return result


def rows(count=10, **changes):
    return [project(event(str(i), NOW-10+i*.2, **changes)) for i in range(count)]


def saved(database, values):
    for i, row in enumerate(values):
        observe(database, row, dict(source='isolated', topic='events', partition=0, offset=i))


def test_nine_ten_and_sustained_request_units():
    assert findings(rows(9), Policy()) == []
    hits = findings(rows(11), Policy())
    assert [h['observed'] for h in hits] == [10, 11]
    result = calculate(rows(11), NOW, Policy(), REAL)
    assert result['signalCount'] == 2
    assert result['signalAttempts'] == 1
    assert result['actionCount'] == 0
    assert all(h['mode'] == 'observe' for h in hits)


@pytest.mark.parametrize('gap,expected', [(3,1),(3.00001,0)])
def test_inclusive_sliding_boundary(gap, expected):
    values = [project(event(str(i),NOW-10)) for i in range(9)]
    values.append(project(event('last',NOW-10+gap)))
    assert len(findings(values, Policy())) == expected


def test_same_time_duplicates_reordered_and_separate_contexts():
    values = [project(event(str(i),NOW-10)) for i in range(10)]
    assert findings(values,Policy()) == findings(list(reversed(values))+values,Policy())
    values[-1] = project(event('other',NOW-10,userId='other'))
    assert not findings(values,Policy())
    values[-1] = project(event('other',NOW-10,bookingAttemptId='other'))
    assert not findings(values,Policy())
    values[-1] = project(event('other',NOW-10,isSimulation=True))
    assert not findings(values,Policy())


def test_unknown_claims_browser_heartbeat_and_no_policy_are_not_detections():
    assert project(event('browser',source='browser')) is None
    unknown = project(event('unknown',details=dict(action='confirm',contextKnown=False,
        observationRequestId='request',answer='secret',cookie='secret')))
    assert unknown['attempt'] is None and unknown['simulation'] is None
    assert 'answer' not in unknown and 'cookie' not in unknown
    assert not findings(rows(10,details=dict(action='heartbeat',contextKnown=True,observationRequestId=None)),Policy())
    result=calculate(rows(),NOW,Policy(enabled=False),REAL)
    assert result['signalCount'] is None and not result['detections']


def test_action_dedup_and_stage_counts_are_separate():
    receipt=project(event('receipt',details=dict(action='verify',contextKnown=True,observationRequestId='r')))
    failure=project(event('wrong',kind='captcha_wrong',result='failure',errorCode='CAPTCHA_WRONG',
        details=dict(observationRequestId='r',wrongCount=5)))
    limit=project(event('limit',kind='captcha_limit_reached',result='failure',errorCode='CAPTCHA_LIMIT',
        details=dict(observationRequestId='r',wrongCount=5)))
    duplicate=project(event('duplicate',kind='booking_access_rejected',result='rejected',errorCode='CAPTCHA_LIMIT',
        details=dict(observationRequestId='r')))
    document=calculate([receipt,failure,limit,duplicate,limit],NOW,Policy(),REAL)
    assert document['signalCount'] is None
    assert document['actionCount']==1 and document['actionAttempts']==1
    captcha=next(s for s in document['stages'] if s['key']=='captcha')
    assert captcha['requests']==1 and captcha['events']==2
    assert document['actions'][0]['observed']==5
    validate(document)


def test_duration_requires_unique_request_correlation_and_preserves_missing():
    received=project(event('r',NOW-10))
    success=project(event('ok',NOW-9,kind='booking_confirm_success',
        details=dict(observationRequestId='req-r',bookingId='B1')))
    other=project(event('other',NOW-8,kind='booking_confirm_success',details=dict(bookingId='B2')))
    stats=calculate([received,success,other],NOW,Policy(),REAL)['durations'][-1]
    assert (stats['samples'],stats['unavailable'],stats['mean'],stats['p95'])==(1,1,1,1)
    unavailable=calculate([success],NOW,Policy(),REAL)['durations'][-1]
    assert unavailable['samples']==0 and unavailable['mean'] is None


def test_durable_conflict_capacity_and_no_partial_pointer(monkeypatch):
    from early_monitor import store
    db=mongomock.MongoClient(tz_aware=True).test
    values=rows()
    saved(db,values)
    refresh(db,NOW)
    old=deepcopy(db.early_public.find_one())
    refresh(db,NOW)
    assert db.early_public.find_one()==old
    assert db.early_detections.count_documents({})==1
    corrupted=project(event('0',NOW-50))
    with pytest.raises(ValueError,match='Conflicting'):
        observe(db,corrupted,{})
    monkeypatch.setattr(store,'MAX_OBSERVATIONS',1)
    with pytest.raises(ValueError,match='capacity'):
        refresh(db,NOW+1)
    assert db.early_public.find_one()==old
    assert db.early_health.find_one()['status']=='blocked'
    refresh(db,NOW+2)  # 실패 후 타이머만으로 ready를 되살리지 않는다.
    assert db.early_health.find_one()['status']=='blocked'


def test_snapshot_failure_keeps_pointer_and_retry_recovers(monkeypatch):
    db=mongomock.MongoClient(tz_aware=True).test
    saved(db,rows())
    refresh(db,NOW)
    old=deepcopy(db.early_public.find_one())
    original=db.early_versions.update_one
    monkeypatch.setattr(db.early_versions,'update_one',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('write')))
    with pytest.raises(RuntimeError): refresh(db,NOW+1)
    assert db.early_public.find_one()==old
    monkeypatch.setattr(db.early_versions,'update_one',original)
    refresh(db,NOW+1,allow_recovery=True)
    assert db.early_health.find_one()['status']=='ready'
    assert db.early_detections.count_documents({})==1


def test_simulation_scopes_and_unobserved_values_are_separate():
    db=mongomock.MongoClient(tz_aware=True).test
    values=rows(9)+rows(10,isSimulation=True)
    # 실제·시뮬레이션 입력은 서로 다른 원본 이벤트 식별자여야 한다.
    values=rows(9)+[project(event('sim'+str(i),NOW-10+i*.2,isSimulation=True)) for i in range(10)]
    saved(db,values)
    refresh(db,NOW)
    pointer=db.early_public.find_one()
    payload=db.early_versions.find_one({'_id':pointer['version']})['data']
    assert 'all' not in payload['scopes']
    assert payload['scopes'][REAL]['signalCount']==0
    assert payload['scopes']['[true,null,null]']['signalCount']==1
    assert payload['scopes']['[null,null,null]']['stages'][0]['events'] is None


def test_late_data_recalculates_two_hour_history_and_context():
    db=mongomock.MongoClient(tz_aware=True).test
    values=[project(event(str(i),NOW-4000+i*.1)) for i in range(9)]
    saved(db,values)
    refresh(db,NOW)
    assert db.early_detections.count_documents({})==0
    saved(db,[project(event('late',NOW-3999))])
    refresh(db,NOW+1)
    assert db.early_detections.count_documents({'status':'active'})==1
    doc=db.early_versions.find_one({'_id':db.early_public.find_one()['version']})['data']['scopes'][REAL]
    assert doc['detections']==[]  # 화면 1시간과 판정 보존 2시간은 별개다.


def test_policy_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv('EARLY_MONITOR_SECONDS','nan')
    with pytest.raises(ValueError): Policy.load()


def test_queue_expiry_requires_observed_queue_stage():
    values=[project(event('queue',kind='connection_timeout',errorCode='EXPIRED',
                         details=dict(observationRequestId='q',observationStage='queue'))),
            project(event('selection',kind='connection_timeout',errorCode='EXPIRED',
                         details=dict(observationRequestId='s',observationStage='select'))),
            project(event('unknown',kind='connection_timeout',errorCode='EXPIRED',details={}))]
    document=calculate(values,NOW,Policy(),REAL)
    queue=next(stage for stage in document['stages'] if stage['key']=='queue')
    assert queue['outcomes']['connection_timeout']==1
    assert document['unknownQueueStage']==1
    assert document['actionCount']==2
    validate(document)
