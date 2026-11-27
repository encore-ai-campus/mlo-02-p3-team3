"""QA 'DB 원인 분석' 링크(run_id) 연결 검사 · 거부 없음 · 표시만 · 사전 관측 기본 범위"""
from copy import deepcopy

import mongomock
import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.early_monitor.reader import ObservationReadError
from published_flow_fixture import published_documents

QA_RUN_ID = 'run-20261127-143015-a3f9'


@pytest.fixture
def web(admin_settings, monkeypatch):
    client = mongomock.MongoClient('mongodb://localhost/flow', tz_aware=True)
    current, series = published_documents()
    client.flow.flow_read_current.insert_one(deepcopy(current))
    client.flow.flow_read_series.insert_one(deepcopy(series))
    monkeypatch.setattr('ticket_guard_admin.user_flow.reader.MongoClient', lambda *a, **kw: client)
    monkeypatch.setattr('ticket_guard_admin.qa_run.reader.MongoClient', lambda *a, **kw: client)
    admin_settings.mongo_uri = 'mongodb://unused/flow'
    return TestClient(create_admin_dashboard_app(admin_settings), headers={'X-Admin-User': 'manager'})


@pytest.fixture
def early_scopes(monkeypatch):
    requested = []

    def read(uri, scope):
        requested.append(scope)
        raise ObservationReadError('시험용')
    monkeypatch.setattr('ticket_guard_admin.user_flow.pages.read_early', read)
    return requested


@pytest.mark.parametrize('path', ['/', '/metrics'])
def test_qa_run_id_is_not_rejected_as_filter(web, path):
    page = web.get(f'{path}?run_id={QA_RUN_ID}').text
    assert 'data-flow-state="unsupported_filter"' not in page
    assert '조회 조건 확인' not in page


def test_unknown_filter_is_still_rejected_with_run_id(web):
    assert 'data-flow-state="unsupported_filter"' in web.get(f'/?run_id={QA_RUN_ID}&show=only-show').text


def test_qa_run_link_is_shown_and_kept_in_early_form(web):
    page = web.get(f'/?run_id={QA_RUN_ID}').text
    assert f'data-qa-run="{QA_RUN_ID}"' in page
    assert f'name="run_id" value="{QA_RUN_ID}"' in page
    assert 'data-qa-run' not in web.get('/').text


@pytest.mark.parametrize('run_id', ['%3Cscript%3Ealert(1)%3C/script%3E', 'run-1', ''])
def test_malformed_run_id_is_not_echoed_or_rejected(web, run_id):
    page = web.get(f'/?run_id={run_id}').text
    assert 'data-qa-run' not in page and '<script>alert(1)' not in page
    assert 'data-flow-state="unsupported_filter"' not in page


def test_duplicate_run_id_is_ignored(web):
    assert 'data-qa-run' not in web.get(f'/?run_id={QA_RUN_ID}&run_id={QA_RUN_ID}').text


def test_early_scope_defaults_to_simulation_only_for_qa_run(web, early_scopes):
    web.get('/')
    web.get(f'/?run_id={QA_RUN_ID}')
    web.get(f'/?run_id={QA_RUN_ID}&early_scope=[false,null,null]')
    assert early_scopes == ['[false,null,null]', '[true,null,null]', '[false,null,null]']
