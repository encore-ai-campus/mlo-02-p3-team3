"""서버 근거·판정 상태·재전달·추가 저장과 게시 실패 회귀 검사."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import mongomock
import pytest

from anomaly.rules import Policy, evaluate, project
from anomaly.store import observe, refresh
from ingestion.mongo_writer import write_partition
from user_flow.events import iso


def event(identity, kind='booking_confirm_request', at=9000, **overrides):
    item = dict(schemaVersion=1, eventId=identity, eventType=kind, occurredAt=iso(at), receivedAt=iso(at),
        source='server', userId='account', bookingAttemptId='attempt', showId='show', scheduleId='evening',
        pageInstanceId='page', result='success', isSimulation=False, details={})
    item.update(overrides)
    return item


def rows(*events):
    return [project(item) for item in events]


def test_no_policy_or_missing_context_is_not_normal(monkeypatch):
    for key in list(__import__('os').environ):
        if key.startswith('ANOMALY_'):
            monkeypatch.delenv(key)
    assert asdict(Policy.load()) == dict.fromkeys(asdict(Policy()))
    observations = rows(event('a'), event('b', userId=None))
    assert all(r['isAnomaly'] is None for r in evaluate(observations, Policy(), 9400)[2])
    result = evaluate(observations, Policy(retry=5), 9400)[2]
    assert result[0]['isAnomaly'] is False
    assert result[1]['isAnomaly'] is None


def test_pending_then_normal_and_late_reordered_anomaly():
    a, b = rows(event('a', at=9000), event('b', at=9002))
    assert evaluate([a], Policy(retry=5), 9010)[2][0]['evaluationStatus'] == 'pending'
    first = evaluate([a], Policy(retry=5), 9400)
    assert first[2][0]['isAnomaly'] is False
    ordered = evaluate([a, b, b], Policy(retry=5), 9400)
    reordered = evaluate([b, a], Policy(retry=5), 9400)
    assert ordered == reordered
    assert len(ordered[1]) == 1 and ordered[1][0]['evidence'][0]['eventId'] == 'a'
    assert ordered[2][1]['isAnomaly'] is True


def test_conflicting_event_id_is_rejected():
    a = project(event('a'))
    b = project(event('a', at=9001))
    with pytest.raises(ValueError, match='Conflicting'):
        evaluate([a, b], Policy(retry=5), 9400)
    database = mongomock.MongoClient().test
    observe(database, a)
    with pytest.raises(ValueError):
        observe(database, b)


def test_polling_browser_flags_and_client_page_signals_are_excluded():
    assert project(event('browser', source='browser', isAnomaly=True)) is None
    assert project(event('poll', kind='server_request', details=dict(request=dict(url='/api/action/',
        body=dict(state='captured', representation='json', value=dict(action='heartbeat')))))) is None
    assert project(event('page', kind='page_enter')) is None


@pytest.mark.parametrize('details', [dict(request=None), dict(request=dict(url=None)),
    dict(request=dict(url='http://[', body=dict(state='captured', representation='json', value=dict(action='confirm')))),
    dict(request=dict(url='/api/action/'))])
def test_incomplete_capture_is_not_a_batch_failure(details):
    assert project(event('partial', 'server_request', details=details)) is None


def test_missing_optional_capture_sections_preserve_business_observation():
    row = project(event('business', details=dict(request=None, response=None, timing=None)))
    assert row['kind'] == 'booking_confirm_request' and row['duration'] is None


def test_chart_does_not_publish_zero_without_rule_specific_evidence():
    from anomaly.store import publication
    data, findings, decisions = evaluate(rows(event('start', 'queue_enter')), Policy(overlap=True,retry=5), 9400)
    result = publication(data, findings, decisions, Policy(overlap=True,retry=5), 9400)
    point = next(point for point in result['series'] if point['at'] == iso(9000))
    assert point['overlap'] is None and point['retry'] is None


def test_overlap_requires_server_interval_and_attempt_scope():
    def request(identity, at, duration, attempt='attempt'):
        return project(event(identity, 'server_request', at, bookingAttemptId=attempt,
            details=dict(requestId=identity, request=dict(url='/api/action/',body=dict(state='captured',representation='json',value=dict(action='confirm'))),
                         timing=dict(observationDurationMs=duration), response=dict(status=200))))
    a, b, c = request('a', 9002, 2000), request('b', 9003, 2000), request('c', 9003, 2000, 'other')
    result = evaluate([c, b, a], Policy(overlap=True), 9400)
    assert [r['eventId'] for r in result[1]] == ['b']
    assert result[1][0]['observed'] == 1


def test_purchase_success_dedup_units_and_scope():
    events = rows(event('start', 'queue_enter', 8990),
        event('a', 'booking_confirm_success', 9000, details=dict(bookingId='B1', quantity=2)),
        event('copy', 'booking_confirm_success', 9001, details=dict(bookingId='B1', quantity=2)),
        event('b', 'booking_confirm_success', 9005, bookingAttemptId='second', details=dict(bookingId='B2', quantity=1)),
        event('other', 'booking_confirm_success', 9006, scheduleId='other', details=dict(bookingId='B3', quantity=4)))
    _, signals, _ = evaluate(events, Policy(fast=15, purchases=10, limit=2), 9400)
    assert [(f['rule'], f['observed']) for f in signals if f['eventId']=='b'] == [('purchases', 2), ('limit', 3)]
    assert any(f['rule']=='fast' and f['observed']==10 for f in signals)
    assert not any(f['eventId']=='copy' for f in signals)


def test_pages_is_observation_not_shared_account_verdict():
    result = evaluate(rows(event('a'), event('b', at=9002, pageInstanceId='other')), Policy(pages=10), 9400)
    assert result[1][0]['label'] == '다중 페이지 계정'
    assert 'bot' not in json.dumps(result[1])


def test_late_stock_failure_retracts_signal_but_preserves_history():
    database = mongomock.MongoClient().test
    for row in rows(event('a'), event('b', at=9002)):
        observe(database, row)
        database.clean_events.insert_one({'_id':row['id']})
    refresh(database, Policy(retry=5), 9400)
    assert database.anomaly_events.find_one()['isAnomaly'] is True
    observe(database, project(event('failure', 'booking_confirm_fail', 9001, errorCode='SEAT_SOLD', result='failure')))
    refresh(database, Policy(retry=5), 9400)
    assert database.anomaly_events.count_documents({}) == 1
    assert database.anomaly_events.find_one()['isAnomaly'] is False
    assert database.anomaly_events.find_one()['evaluationStatus'] == 'retracted'
    assert database.clean_events.find_one({'_id':'b'})['isAnomaly'] is False


def test_capture_scope_is_inferred_only_from_unique_server_attempt():
    row = project(event('capture', 'server_request', 9002, schemaVersion=2, scheduleId=None, isSimulation=None,
        details=dict(requestId='request', request=dict(url='/api/action/', body=dict(state='captured',representation='json',value=dict(action='confirm'))),
                     timing=dict(observationDurationMs=1000), response=dict(status=200))))
    result = evaluate([project(event('anchor')), row], Policy(overlap=True), 9400)
    assert result[0][1]['schedule']=='evening' and result[0][1]['simulation'] is False
    assert result[2][1]['isAnomaly'] is False


def test_publication_idempotence_boolean_and_failure_recovery(monkeypatch):
    database = mongomock.MongoClient(tz_aware=True).test
    for item in rows(event('a'), event('b', at=9002)):
        observe(database, item)
        database.clean_events.insert_one(dict(_id=item['id']))
    policy = Policy(retry=5)
    refresh(database, policy, 9400)
    before = list(database.anomaly_events.find())
    refresh(database, policy, 9400)
    assert list(database.anomaly_events.find()) == before
    assert database.clean_events.find_one({'_id':'a'})['isAnomaly'] is False
    assert database.clean_events.find_one({'_id':'b'})['isAnomaly'] is True
    assert database.anomaly_public.find_one({'_id':'all'})['attempts'] == 1
    real = database.anomaly_public.replace_one
    monkeypatch.setattr(database.anomaly_public, 'replace_one', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('write failed')))
    with pytest.raises(RuntimeError):
        refresh(database, policy, 9400)
    assert database.anomaly_health.find_one() ['status'] == 'blocked'
    monkeypatch.setattr(database.anomaly_public, 'replace_one', real)
    refresh(database, policy, 9400)
    assert database.anomaly_health.find_one()['status'] == 'ready'
    assert database.anomaly_events.count_documents({}) == 1


def test_retention_capacity_and_missing_fields_do_not_become_zero(monkeypatch):
    from anomaly import store
    database = mongomock.MongoClient().test
    observe(database, project(event('a', userId=None)))
    refresh(database, Policy(retry=5), 9400)
    assert database.anomaly_public.find_one({'_id':'all'})['counts']['retry'] is None
    monkeypatch.setattr(store, 'MAX_OBSERVATIONS', 0)
    with pytest.raises(ValueError, match='capacity'):
        refresh(database, Policy(), 9400)
    assert database.anomaly_health.find_one()['status'] == 'blocked'


def test_partition_preserves_clean_and_quarantine_without_raw_write(monkeypatch):
    import ingestion.mongo_writer as writer
    client = mongomock.MongoClient()
    monkeypatch.setattr(writer, 'client_for', lambda config: client)
    def row(value, offset):
        return SimpleNamespace(topic='events', partition=0, offset=offset, key=None, value=value,
            timestamp=datetime.now(timezone.utc), timestampType=0)
    records = [row(json.dumps(event('a')).encode(), 0), row(b'invalid', 1)]
    config = SimpleNamespace(database='test', timeout_ms=3000, retries=1, bulk_size=100)
    write_partition(records, config)
    write_partition(records, config)
    assert client.test.clean_events.count_documents({}) == 1
    assert client.test.quarantine_events.count_documents({}) == 1
    assert client.test.anomaly_observations.count_documents({}) == 1
    assert client.test.clean_events.find_one()['isAnomaly'] is None
    assert 'raw_events' not in client.test.list_collection_names()


@pytest.mark.parametrize('name,value', [('FAST_SECONDS','nan'), ('RETRY_SECONDS','-1'), ('ACCOUNT_LIMIT','1.5'), ('OVERLAP_ENABLED','yes')])
def test_invalid_policy_rejected(monkeypatch, name, value):
    monkeypatch.setenv('ANOMALY_'+name, value)
    with pytest.raises(ValueError):
        Policy.load()
