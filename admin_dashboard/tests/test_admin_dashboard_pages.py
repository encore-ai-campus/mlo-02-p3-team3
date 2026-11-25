"""관리자 05 화면 · DB 미연결과 게이트웨이 인증 경계 회귀 검사"""
import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.preview_data import build_preview_data
from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.dependencies import ADMIN_USER_HEADER


@pytest.mark.parametrize('path', ['/db', '/preview', '/preview?anomaly_mode=post'])
def test_retired_pages_are_not_available(admin_settings, path):
    client = TestClient(create_admin_dashboard_app(admin_settings))
    assert client.get(path).status_code == 404
    assert client.get(path, headers={ADMIN_USER_HEADER: 'manager'}).status_code == 404


def test_live_views_and_refresh_survive_page_removal(admin_settings):
    client = TestClient(create_admin_dashboard_app(admin_settings))
    headers = {ADMIN_USER_HEADER: 'manager'}
    assert client.get('/').status_code == 403
    assert client.get('/metrics').status_code == 403
    for path in ('/', '/metrics'):
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        for view in ('flow', 'network', 'anomaly'):
            assert f'data-preview-view="{view}"' in response.text
        assert 'href="/admin/db"' not in response.text
        assert 'href="/admin/preview"' not in response.text
    assert 'monitor_refresh.js' in client.get('/', headers=headers).text
    for filename in ('screen_05_live_dashboard.html', 'monitor_preview.html'):
        from ticket_guard_admin.pages import ADMIN_DASHBOARD_DIR
        assert not (ADMIN_DASHBOARD_DIR / 'templates' / filename).exists()


def test_preview_funnel_and_review_counts_are_consistent():
    preview = build_preview_data()
    assert [count for _, count in preview["funnel"]] == [1000, 900, 810, 740, 620]
    assert len(preview["users"]) == 12
    assert len({user["id"] for user in preview["users"]}) == 12
    assert {priority: sum(user["priority"] == priority for user in preview["users"])
            for priority in ("우선 검토", "관찰", "데이터 확인")} == {"우선 검토": 3, "관찰": 7, "데이터 확인": 2}
