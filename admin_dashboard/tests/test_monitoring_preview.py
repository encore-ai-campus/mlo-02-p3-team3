"""
[홍석환] 세 관점 시안의 데이터·페이지 회귀 검사
표시 수치의 정합성, 제안 지표의 구분, 시안 인증과 정적 자원을 확인한다.
"""
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.monitoring_preview_data import build_monitoring_preview


def test_flow_preview_cards_graph_and_cohorts_agree():
    flow = build_monitoring_preview()['flow']
    assert flow['current'] == sum(node['current'] for node in flow['nodes']) == flow['presence'][-1]['value']
    assert flow['starts'] == sum(point['denominator'] for point in flow['booking'])
    assert flow['completed'] == sum(point['numerator'] for point in flow['booking'])
    assert flow['ratio'] == flow['completed'] / flow['starts']
    for edge, left, right in zip(flow['edges'], flow['nodes'], flow['nodes'][1:]):
        assert edge['denominator'] == left['entered']
        assert edge['numerator'] == right['entered']
        assert edge['ratio'] == edge['numerator'] / edge['denominator']
    for point in flow['booking']:
        assert 0 <= point['numerator'] <= point['denominator']
    assert any(point['value'] is None and not point['observed'] for point in flow['presence'])
    assert {'backward', 'retry', 'cancel'} == {route['kind'] for route in flow['secondary']}


def test_network_totals_and_mean_are_consistent():
    network = build_monitoring_preview()['network']
    for field in ('requests', 'errors', 'missing'):
        assert network[field] == sum(row[field] for row in network['paths'])
    assert network['requests'] == sum(point['value'] for point in network['requestsSeries'])
    assert network['errors'] == sum(point['value'] for point in network['errorsSeries'])
    assert all(0 <= e['value'] <= r['value'] for e, r in zip(network['errorsSeries'], network['requestsSeries']))
    for field in ('browserMean', 'serverMean'):
        assert network[field] == pytest.approx(sum(row[field]*row['requests'] for row in network['paths']) / network['requests'])


def test_anomaly_summary_is_based_on_sample_evidence_only():
    anomaly = build_monitoring_preview()['anomaly']
    counts = Counter(event['kind'] for subject in anomaly['subjects'] for event in subject['events'])
    assert anomaly['total'] == sum(counts.values())
    assert all(kind['count'] == counts[kind['id']] for kind in anomaly['kinds'])
    assert len({subject['id'] for subject in anomaly['subjects']}) == len(anomaly['subjects'])
    assert all('score' not in subject and 'groundTruth' not in subject for subject in anomaly['subjects'])


def test_three_views_render_without_database_or_external_assets(admin_settings, monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail('시안에서 실제 DB 조회 시도')
    monkeypatch.setattr('ticket_guard_admin.mongo_reader.MongoClient', reject)
    client = TestClient(create_admin_dashboard_app(admin_settings), headers={'X-Admin-User': 'preview-manager'})
    page = client.get('/preview?run_id=%3Cscript%3E')
    assert page.status_code == 200
    assert all(f'data-preview-view="{view}"' in page.text for view in ('flow','network','anomaly'))
    assert '시안용 샘플 데이터' in page.text and page.text.count('검토용 제안') >= 2
    assert '확정 지표' in page.text and '실제 DB 연결 없음' in page.text
    assert '&lt;script&gt;' in page.text
    assert 'cdn.jsdelivr.net' not in page.text
    for filename in ('monitor_common.css','monitor_flow.css','monitor_navigation.js','monitor_charts.js','monitor_flow.js','monitor_anomaly.js'):
        response = client.get('/admin-static/' + filename)
        assert response.status_code == 200
        if filename.endswith('.js'):
            assert 'setInterval(' not in response.text and 'fetch(' not in response.text
    assert TestClient(create_admin_dashboard_app(admin_settings)).get('/preview').status_code == 403
