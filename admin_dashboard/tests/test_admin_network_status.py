from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.app import AdminSettings, create_admin_dashboard_app
from ticket_guard_admin.network_status.reader import NetworkReadError, validate


def publication():
    start = datetime(2026, 11, 22, 4, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    counts = dict(requests=2, http4xx=0, http5xx=1, measured=2, slow=None)
    buckets = [dict(at=(start + timedelta(minutes=5 * i)).isoformat(), observed=i in (1, 2), gateway=None,
                    **(counts if i == 1 else {key: 0 if value is not None else None for key, value in counts.items()}
                       if i == 2 else dict.fromkeys(counts))) for i in range(12)]
    data = dict(contractVersion=1, windowStart=start.isoformat(), windowEnd=end.isoformat(), bucketSeconds=300,
                latestRequestIngestedAt=end.isoformat(), thresholdMs=None, summary=counts,
                features=[dict(id='booking_api', **counts)],
                sources=dict(django='observed', gateway='unobserved', health='unobserved'))
    current = dict(_id='all', version='verified', result=dict(_id='verified', data=data, publishedAt=end))
    series = dict(_id='all', version='verified', result=dict(
        _id='verified', windowStart=start.isoformat(), windowEnd=end.isoformat(), buckets=buckets))
    return current, series


def test_network_renders_observed_zero_separately_from_a_chart_gap(monkeypatch):
    monkeypatch.setattr('ticket_guard_admin.user_flow.pages.read_network', lambda uri: validate(*publication()))
    app = create_admin_dashboard_app(AdminSettings(mongo_uri='', _env_file=None))
    client = TestClient(app)
    assert client.get('/').status_code == 403
    response = client.get('/', headers={'X-Admin-User': 'manager'})
    network = response.text.split('data-preview-view="network"', 1)[1].split('data-preview-view="anomaly"', 1)[0]
    assert response.status_code == 200
    assert 'data-network-metric="server-5xx">50.00%</strong>' in network
    assert 'class="mp-network-zero requests"' in network and 'class="mp-network-gap"' in network
    assert '공통 예매 API</th><td>2</td><td>1</td><td>0</td><td>—</td>' in network
    assert '게이트웨이 오류 · 미관측' in network


def test_reader_rejects_version_and_aggregate_disagreement():
    current, series = publication()
    altered = deepcopy(series)
    altered['version'] = 'another'
    with pytest.raises(NetworkReadError):
        validate(current, altered)
    altered = deepcopy(series)
    altered['result']['buckets'][1]['http5xx'] = 0
    with pytest.raises(NetworkReadError):
        validate(current, altered)
