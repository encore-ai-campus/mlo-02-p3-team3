"""QA Run 구간 조회 · run_id 시작 시각 · run_to 종료 시각 · 시험 계정 이벤트만 · 집계값만 화면에"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.qa_run.reader import QaRunReadError, build_run_window, read_qa_run
from published_flow_fixture import published_documents

RUN_ID = 'run-20261127-050935-86ba'
STARTED_AT = datetime(2026, 11, 27, 5, 9, 35, tzinfo=timezone.utc)
NOW = STARTED_AT + timedelta(hours=3)


def event(kind, seconds, user='qa-user-0001', attempt='attempt-1', is_simulation=True, **extra):
    return {'eventId': f'{attempt}-{kind}-{seconds}', 'eventType': kind, 'userId': user, 'bookingAttemptId': attempt,
            'occurredAt': STARTED_AT + timedelta(seconds=seconds), 'result': 'success', 'isSimulation': is_simulation,
            'details': {'password': 'never-expose'}, **extra}


@pytest.fixture
def system_client():
    client = mongomock.MongoClient('mongodb://localhost/ticketguard', tz_aware=True)
    client.ticketguard.clean_events.insert_many([
        event('queue_enter', 10), event('queue_admitted', 20), event('booking_confirm_success', 40),
        event('queue_enter', 12, user='qa-user-0002', attempt='attempt-2'),
        event('login_fail', 15, user='qa-user-0003', attempt=None, result='rejected', errorCode='LOGIN_LOCKED'),
        event('queue_enter', 11, user='real-user', attempt='attempt-real', is_simulation=False),  # 실사용자 제외
        event('queue_enter', -600, attempt='attempt-before'),  # Run 시작 전 제외
        event('queue_enter', 900, attempt='attempt-after'),  # 종료 후 제외
    ])
    return client


def test_window_uses_run_id_start_and_given_finish():
    start, end, has_finish = build_run_window(RUN_ID, '2026-11-27T05:20:00+00:00', NOW)
    assert start == STARTED_AT - timedelta(seconds=5) and end == datetime(2026, 11, 27, 5, 20, tzinfo=timezone.utc) and has_finish


@pytest.mark.parametrize('run_to', [None, 'not-a-time', '2026-11-27T04:00:00+00:00', '2026-11-28T05:00:00+00:00'])
def test_missing_or_wrong_finish_falls_back_to_sixty_minutes(run_to):
    _, end, has_finish = build_run_window(RUN_ID, run_to, NOW)
    assert end == STARTED_AT + timedelta(minutes=60) and not has_finish


def test_window_never_goes_past_now():
    _, end, _ = build_run_window(RUN_ID, None, STARTED_AT + timedelta(minutes=2))
    assert end == STARTED_AT + timedelta(minutes=2)


def test_reader_counts_only_test_accounts_inside_run(system_client, monkeypatch):
    monkeypatch.setattr('ticket_guard_admin.qa_run.reader.MongoClient', lambda *a, **kw: system_client)
    result = read_qa_run('mongodb://localhost/ticketguard', RUN_ID, '2026-11-27T05:19:35+00:00', now=NOW)
    assert result['matched'] == 5 and result['user_count'] == 3
    assert result['starts'] == 2 and result['completed'] == 1 and result['conversion'] == 50.0
    assert result['failures'] == [('LOGIN_LOCKED', 1, 0)] and result['rejected_users'] == 1
    assert result['window_label'] == '11-27 14:09:30 ~ 11-27 14:19:35 KST'
    assert 'never-expose' not in str(result) and 'real-user' not in str(result)


def test_reader_rejects_missing_uri_and_bad_run_id():
    with pytest.raises(QaRunReadError):
        read_qa_run('', RUN_ID)
    with pytest.raises(QaRunReadError):
        read_qa_run('mongodb://localhost/ticketguard', 'run-1')


@pytest.fixture
def web(admin_settings, system_client, monkeypatch):
    flow_client = mongomock.MongoClient('mongodb://localhost/flow', tz_aware=True)
    current, series = published_documents()
    flow_client.flow.flow_read_current.insert_one(deepcopy(current))
    flow_client.flow.flow_read_series.insert_one(deepcopy(series))
    monkeypatch.setattr('ticket_guard_admin.user_flow.reader.MongoClient', lambda *a, **kw: flow_client)
    monkeypatch.setattr('ticket_guard_admin.qa_run.reader.MongoClient', lambda *a, **kw: system_client)
    admin_settings.mongo_uri = 'mongodb://unused/ticketguard'
    return TestClient(create_admin_dashboard_app(admin_settings), headers={'X-Admin-User': 'manager'})


def test_dashboard_shows_run_section_with_counts(web):
    page = web.get(f'/?run_id={RUN_ID}&run_to=2026-11-27T05:19:35%2B00:00').text
    assert 'data-qa-run-state="ready"' in page and 'QA Run 구간 조회' in page
    assert '11-27 14:09:30 ~ 11-27 14:19:35 KST' in page and 'LOGIN_LOCKED' in page
    assert 'data-flow-state="unsupported_filter"' not in page
    assert 'name="run_to" value="2026-11-27T05:19:35+00:00"' in page  # 사전 관측 조회 후에도 구간 유지


def test_run_section_is_not_in_refresh_fragment(web):
    assert 'data-qa-run' not in web.get(f'/metrics?run_id={RUN_ID}').text


def test_dashboard_without_run_id_has_no_run_section(web):
    assert 'QA Run 구간 조회' not in web.get('/').text
