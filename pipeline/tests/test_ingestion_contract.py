"""지연 판정 저장 범위와 서버 예매 필드 보존 계약."""
import json

import mongomock
import pytest

from anomaly.rules import Policy, project
from anomaly.store import observe, refresh
from ingestion.normalization import normalize
from user_flow.events import iso


def event(identity, at, kind='booking_confirm_request', **changes):
    item = dict(schemaVersion=1, eventId=identity, eventType=kind,
                occurredAt=iso(at), receivedAt=iso(at), source='server',
                userId='user', bookingAttemptId='attempt', showId='show',
                scheduleId='schedule', pageInstanceId='page', result='success',
                isSimulation=False, details={})
    item.update(changes)
    return item


@pytest.mark.parametrize('age', [4000, 7200])
def test_late_decision_and_retraction_update_clean_within_two_hours(age):
    now = 20000
    at = now - age
    database = mongomock.MongoClient(tz_aware=True).test
    for item in (event('first', at-2), event('second', at)):
        observe(database, project(item))
        database.clean_events.insert_one(dict(_id=item['eventId'], isAnomaly=None))
    refresh(database, Policy(retry=5), now)
    assert database.clean_events.find_one({'_id': 'second'})['isAnomaly'] is True
    assert database.anomaly_events.find_one({'eventId': 'second'})['isAnomaly'] is True
    # 최근 1시간 화면에는 오래된 판정을 섞지 않는다.
    assert database.anomaly_public.find_one({'_id': 'all'})['detections'] == []
    observe(database, project(event('stock', at-1, 'booking_confirm_fail',
                                   errorCode='SEAT_SOLD', result='failure')))
    refresh(database, Policy(retry=5), now)
    assert database.clean_events.find_one({'_id': 'second'})['isAnomaly'] is False
    finding = database.anomaly_events.find_one({'eventId': 'second'})
    assert finding['isAnomaly'] is False
    assert finding['evaluationStatus'] == 'retracted'


def test_grace_observations_are_context_without_persisting_old_decisions():
    now = 20000
    database = mongomock.MongoClient().test
    for item in (event('a', now-7203), event('b', now-7201)):
        observe(database, project(item))
        database.clean_events.insert_one(dict(_id=item['eventId'], isAnomaly=None))
    refresh(database, Policy(retry=5), now)
    assert database.anomaly_events.count_documents({}) == 0
    assert database.clean_events.find_one({'_id': 'b'})['isAnomaly'] is None


def test_booking_details_are_preserved_without_copying_request_secrets():
    details = dict(bookingId='B1', quantity=2, totalAmount=120000, seatType='reserved',
                   seats=['A1', 'A2'], request={'authorization': 'private'})
    item = event('purchase', 1000, 'booking_confirm_success', details=details)
    clean, quarantine = normalize(json.dumps(item).encode(), {'_id': 'transport'})
    assert quarantine is None
    assert {key: clean[key] for key in ('bookingId', 'quantity', 'totalAmount', 'seatType', 'seats')} == {
        key: details[key] for key in ('bookingId', 'quantity', 'totalAmount', 'seatType', 'seats')}
    assert 'request' not in clean and 'details' not in clean


def test_missing_booking_details_are_null_and_browser_claims_are_not_copied():
    item = event('purchase', 1000, 'booking_confirm_success')
    clean, _ = normalize(json.dumps(item).encode(), {'_id': 'transport'})
    assert all(clean[key] is None for key in ('bookingId', 'quantity', 'totalAmount', 'seatType', 'seats'))
    item.update(source='browser', details={'bookingId': 'forged'})
    clean, _ = normalize(json.dumps(item).encode(), {'_id': 'transport'})
    assert 'bookingId' not in clean


@pytest.mark.parametrize('details', [dict(quantity=True), dict(quantity=-1),
                                    dict(totalAmount=-1), dict(totalAmount=float('inf')),
                                    dict(seats='A1'), dict(seats=[None]), dict(bookingId=123)])
def test_invalid_booking_fields_are_quarantined(details):
    item = event('purchase', 1000, 'booking_confirm_success', details=details)
    clean, quarantine = normalize(json.dumps(item).encode(), {'_id': 'transport'})
    assert clean is None
    assert quarantine == {'_id': 'transport', 'reason': 'invalid_event'}
