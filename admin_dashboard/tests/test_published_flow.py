"""
[홍석환] 실제 공개 지표 조회와 화면 연결 검사
읽기 경계·버전 일치·계약·오류·시안 분리 및 기존 화면 보존을 검증한다.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import mongomock
import pytest
from bson.int64 import Int64
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.user_flow.contracts import FlowError, validate, scope_key
from ticket_guard_admin.user_flow.presentation import for_display
from ticket_guard_admin.user_flow.reader import PublishedFlowReader
from published_flow_fixture import published_documents


def configured_reader(monkeypatch, docs=None):
    client = mongomock.MongoClient('mongodb://localhost/flow', tz_aware=True)
    docs = docs or published_documents()
    client.flow.flow_read_current.insert_one(deepcopy(docs[0]))
    client.flow.flow_read_series.insert_one(deepcopy(docs[1]))
    monkeypatch.setattr('ticket_guard_admin.user_flow.reader.MongoClient', lambda *a, **kw: client)
    return PublishedFlowReader('mongodb://unused/flow'), client


def test_reader_uses_public_views_and_preserves_all_five_metrics(monkeypatch):
    reader, client = configured_reader(monkeypatch)
    result = reader.read()
    display = for_display(result)
    assert display['current'] == 3
    assert display['starts'] == 4 and display['completed'] == 2 and display['ratio'] == .5
    assert [node['id'] for node in display['nodes']] == ['queue','other']
    assert display['nodes'][0]['mean'] == 5.5 and display['nodes'][0]['median'] == 4.5
    assert display['nodes'][1]['samples'] == 0 and display['nodes'][1]['mean'] is None
    assert display['presence'][0]['value'] == 0
    assert display['presence'][1]['value'] is None and not display['presence'][1]['observed']
    assert display['count_unit'] == '개' and display['counting_label'] == '브라우저 세션'
    assert not client.flow.flow_versions.find_one() and not client.flow.flow_public.find_one()


def test_series_is_clipped_to_current_twelve_hours_and_kst(monkeypatch):
    now = datetime(2026, 11, 12, 3, tzinfo=timezone.utc)
    reader, _ = configured_reader(monkeypatch, published_documents(now))
    display = for_display(reader.read(), now=now)
    assert len(display['presence']) == 3
    assert display['as_of_label'] == '2026.11.12 12:00:00 KST'
    later = for_display(reader.read(), now=now+timedelta(hours=13))
    assert later['presence'] == [] and later['booking'] == []
    assert later['current'] == 3  # 기준 시각의 최신값을 시계열에서 다시 만들지 않는다.


def test_public_version_change_is_retried(monkeypatch):
    c1,s1 = published_documents(version='v1'); c2,s2 = published_documents(version='v2')
    client=MagicMock(); db=client.__enter__.return_value.get_default_database.return_value
    db.name='flow'
    db.flow_read_current.find_one.side_effect=[c1,c2]
    db.flow_read_series.find_one.side_effect=[s2,s2]
    db.flow_read_current.find.return_value.limit.return_value.max_time_ms.return_value=[{'_id':'all'}]
    monkeypatch.setattr('ticket_guard_admin.user_flow.reader.MongoClient',lambda *a,**kw:client)
    assert PublishedFlowReader('mongodb://unused/flow').read()['version']=='v2'
    assert db.flow_read_current.find_one.call_count==2


def test_repeated_or_inner_version_mismatch_is_not_mixed(monkeypatch):
    c,s=published_documents(); s['result']['version']='other-version'
    reader,client=configured_reader(monkeypatch,(c,s))
    with pytest.raises(FlowError) as failure: reader.read()
    assert failure.value.code=='version_mismatch'


@pytest.mark.parametrize('code,expected',[(13,'permission_denied'),(18,'authentication_failed'),(999,'query_failed')])
def test_operation_errors_are_safe(monkeypatch,code,expected):
    def fail(*a,**kw): raise OperationFailure('secret-uri-password',code=code)
    monkeypatch.setattr('ticket_guard_admin.user_flow.reader.MongoClient',fail)
    with pytest.raises(FlowError) as error: PublishedFlowReader('mongodb://unused/flow').read()
    assert error.value.code==expected and 'secret' not in str(error.value)


def test_connection_error_and_missing_uri(monkeypatch):
    def fail(*a,**kw): raise ServerSelectionTimeoutError('secret')
    monkeypatch.setattr('ticket_guard_admin.user_flow.reader.MongoClient',fail)
    with pytest.raises(FlowError) as error: PublishedFlowReader('mongodb://unused/flow').read()
    assert error.value.code=='connection_failed'
    with pytest.raises(FlowError) as error: PublishedFlowReader('').read()
    assert error.value.code=='connection_config'


def test_no_publication_and_missing_scope_are_distinct(monkeypatch):
    reader,client=configured_reader(monkeypatch)
    client.flow.flow_read_current.delete_many({})
    with pytest.raises(FlowError) as error: reader.read()
    assert error.value.code=='no_publication'
    with pytest.raises(FlowError) as error: reader.read('[true,"show","schedule"]')
    assert error.value.code=='scope_not_found'


@pytest.mark.parametrize('mutation',[
    lambda m: m.update(contractVersion=2),
    lambda m: m.update(current=99),
    lambda m: m['nodes'][0]['dwell'].update(meanSeconds=0),
    lambda m: m['nodes'][1].update(id='other'),
    lambda m: m['edges'][0].update(source='missing'),
    lambda m: m['edges'][0].update(kind='unrecognized'),
    lambda m: m['booking'].update(ratio=50),
    lambda m: m['booking'].update(numerator=True),
])
def test_invalid_contract_is_not_guessed(mutation):
    c,s=published_documents(); mutation(c['result']['metrics'])
    with pytest.raises(FlowError) as error: validate(c,s,'all')
    assert error.value.code=='invalid_contract'


def test_scope_contract_and_exact_combination(monkeypatch):
    scope='[true,"공연","회차"]'
    canonical=scope_key(scope)
    reader,_=configured_reader(monkeypatch,published_documents(scope=canonical))
    assert reader.read(scope)['version']=='test-version-1'
    for invalid in ('show-only','[1,"show","schedule"]','[true,"show"]'):
        with pytest.raises(FlowError): scope_key(invalid)


def test_actual_page_uses_db_data_never_sample(admin_settings,monkeypatch):
    from ticket_guard_admin.network_status.reader import NetworkReadError
    from ticket_guard_admin.anomaly.reader import AnomalyReadError
    from ticket_guard_admin.early_monitor.reader import ObservationReadError

    reader,client=configured_reader(monkeypatch)
    admin_settings.mongo_uri=reader.uri
    def reject(): pytest.fail('실제 화면에서 샘플 데이터 호출')
    monkeypatch.setattr('ticket_guard_admin.monitoring_preview_data.build_monitoring_preview',reject)
    # 이 검사는 흐름 게시물만 제공한다. 다른 조회의 실패를 실제 DB 접속 없이 고정한다.
    for name,error in (
        ('read_network', NetworkReadError('network publication unavailable')),
        ('read_anomaly', AnomalyReadError('anomaly publication unavailable')),
        ('read_early', ObservationReadError('early publication unavailable')),
    ):
        def unavailable(*args, _error=error, **kwargs):
            raise _error
        monkeypatch.setattr('ticket_guard_admin.user_flow.pages.' + name, unavailable)
    response=TestClient(create_admin_dashboard_app(admin_settings),headers={'X-Admin-User':'manager'}).get('/')
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert 'monitor-live-data' in response.text and 'monitor-preview-data' not in response.text
    assert '샘플 데이터' not in response.text and '50.0' in response.text
    assert '5.5초' in response.text and '브라우저 세션' in response.text
    assert response.context['network_error'] is True
    assert response.context['anomaly']['error'] == 'anomaly publication unavailable'
    assert response.context['anomaly']['attempts'] is None
    assert '지표 조회 실패' in response.text
    assert 'early publication unavailable' in response.text
    assert 'href="/admin/db"' not in response.text
    assert 'href="/admin/preview"' not in response.text


def test_zero_denominator_remains_unavailable(admin_settings,monkeypatch):
    c,s=published_documents(); c['result']['metrics']['booking']={'numerator':0,'denominator':0,'ratio':None}
    reader,_=configured_reader(monkeypatch,(c,s)); admin_settings.mongo_uri=reader.uri
    response=TestClient(create_admin_dashboard_app(admin_settings),headers={'X-Admin-User':'manager'}).get('/')
    assert '집계 불가 · 표본 없음' in response.text
    assert 'None' not in response.text


def test_actual_empty_state_and_unsupported_filter_do_not_show_fake_values(admin_settings,monkeypatch):
    reader,client=configured_reader(monkeypatch); admin_settings.mongo_uri=reader.uri
    client.flow.flow_read_current.delete_many({})
    web=TestClient(create_admin_dashboard_app(admin_settings),headers={'X-Admin-User':'manager'})
    assert 'data-flow-state="no_publication"' in web.get('/').text
    assert 'data-flow-state="unsupported_filter"' in web.get('/?show=only-show').text
    assert 'mp-stage-count' not in web.get('/').text
    assert TestClient(create_admin_dashboard_app(admin_settings)).get('/').status_code==403


def test_stage_strings_are_safely_serialized(admin_settings,monkeypatch):
    c,s=published_documents(); c['result']['metrics']['nodes'][0]['label']='</script><script>alert(1)</script>'
    reader,_=configured_reader(monkeypatch,(c,s)); admin_settings.mongo_uri=reader.uri
    response=TestClient(create_admin_dashboard_app(admin_settings),headers={'X-Admin-User':'manager'}).get('/')
    assert '</script><script>alert(1)' not in response.text
    assert '&lt;/script&gt;' in response.text
    assert '\\u003c/script\\u003e' in response.text


def test_unobserved_point_still_requires_explicit_contract_fields():
    c,s=published_documents()
    del s['result']['booking'][1]['ratio']
    with pytest.raises(FlowError) as error: validate(c,s,'all')
    assert error.value.code=='invalid_contract'


def test_bson_integer_counts_are_supported_without_accepting_booleans():
    c,s=published_documents()
    c['result']['metrics']['current']=Int64(3)
    assert validate(c,s,'all')[0]['current']==3


def test_zero_presence_and_selected_scope_render_without_sample_labels(admin_settings, monkeypatch):
    scope = scope_key('[true,"show","schedule"]')
    c, s = published_documents(scope=scope)
    c['result']['metrics']['current'] = 0
    for node in c['result']['metrics']['nodes']:
        node['current'] = 0
    reader, _ = configured_reader(monkeypatch, (c, s))
    admin_settings.mongo_uri = reader.uri
    web = TestClient(create_admin_dashboard_app(admin_settings), headers={'X-Admin-User': 'manager'})
    response = web.get('/', params={'scope': scope})
    assert response.status_code == 200
    assert 'data-flow-state=' not in response.text
    assert '단계별 샘플 지표' not in response.text and '0명이' not in response.text
    assert '현재 동시 접속 브라우저 세션' in response.text
    assert '실행·검증 실패 여부는 조회하지 않습니다' in response.text
    assert 'monitor_user_flow' not in response.text  # 템플릿은 서버에서 포함된다.
    assert '/admin/admin-static/monitor_navigation.js' in response.text
    assert 'mp-live-toolbar' not in response.text
    assert response.context['scope'] == scope
    assert response.context['flow']['current'] == 0
