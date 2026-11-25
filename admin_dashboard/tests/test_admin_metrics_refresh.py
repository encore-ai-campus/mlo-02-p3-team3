"""실제 화면 전용 부분 렌더링과 관리자 인증 경계를 확인한다."""
from fastapi.testclient import TestClient
from ticket_guard_admin.app import create_admin_dashboard_app
from pathlib import Path
import shutil
import subprocess
import pytest


def test_metrics_requires_admin(admin_settings):
    client = TestClient(create_admin_dashboard_app(admin_settings))
    assert client.get('/metrics').status_code == 403


def test_metrics_returns_same_filtered_panels_without_page_shell(admin_settings):
    client = TestClient(create_admin_dashboard_app(admin_settings))
    headers = {'X-Admin-User': 'manager'}
    response = client.get('/metrics?anomaly_mode=post&early_scope=[false,null,null]', headers=headers)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    for view in ('flow', 'network', 'anomaly'):
        assert f'data-preview-view="{view}"' in response.text
    assert 'data-metrics-data' in response.text
    assert '<html' not in response.text
    assert 'monitor_refresh.js' not in response.text
    live = client.get('/', headers=headers)
    assert 'monitor_refresh.js' in live.text
    assert 'data-url="/admin/metrics"' in live.text
    preview = client.get('/preview', headers=headers)
    assert 'monitor_refresh.js' not in preview.text


def test_polling_overlap_failure_recovery_and_preview_isolation():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for polling behavior checks')
    directory = Path(__file__).parent
    result = subprocess.run(
        [node, str(directory / 'check_metrics_refresh.cjs'),
         str(directory.parent / 'src/ticket_guard_admin/static/monitor_refresh.js')],
        capture_output=True, text=True, encoding='utf-8', timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
