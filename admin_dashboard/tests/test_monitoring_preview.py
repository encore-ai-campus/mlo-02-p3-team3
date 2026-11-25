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
