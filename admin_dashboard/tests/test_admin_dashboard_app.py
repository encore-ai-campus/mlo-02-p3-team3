"""관리자 독립 앱 · QA DB 초기화 금지와 게이트웨이 인증"""
from fastapi.testclient import TestClient
import subprocess
import sys

from ticket_guard_admin.app import AdminSettings, create_admin_dashboard_app
from ticket_guard_admin.mongo_reader import build_dashboard


def test_standalone_admin_does_not_initialize_qa_database():
    # 새 프로세스에서 QA 패키지의 모든 import를 차단하고 관리자 앱 생성.
    code = '''
import importlib.abc
import sys
class RejectQa(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ticket_guard_qa" or fullname.startswith("ticket_guard_qa."):
            raise AssertionError("QA dependency: " + fullname)
sys.meta_path.insert(0, RejectQa())
from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.settings import AdminSettings
create_admin_dashboard_app(AdminSettings(mongo_uri="", _env_file=None))
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
    app = create_admin_dashboard_app(AdminSettings(mongo_uri="", _env_file=None))
    client = TestClient(app)
    assert client.get("/").status_code == 403
    response = client.get("/", headers={"X-Admin-User": "manager"})
    assert response.status_code == 200
    assert "공개 지표 조회 실패" in response.text  # 헤더 상태 표시
    assert "샘플 데이터" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_unpublished_flow_shows_metric_panels_without_numeric_values(monkeypatch):
    from ticket_guard_admin.user_flow.contracts import FlowError

    def unpublished(*args, **kwargs):
        raise FlowError("no_publication", "공개 결과 없음")

    monkeypatch.setattr("ticket_guard_admin.user_flow.pages.PublishedFlowReader.read", unpublished)
    app = create_admin_dashboard_app(AdminSettings(mongo_uri="mongodb://unused/ticketguard", _env_file=None))
    response = TestClient(app).get("/", headers={"X-Admin-User": "manager"})

    assert response.status_code == 200
    assert 'data-flow-state="no_publication"' in response.text
    assert response.text.count('class="mp-value">&#8212;</div>') == 2
    assert 'id="mp-queue-current">&#8212;</div>' in response.text
    assert 'id="mp-flow-graph"' in response.text
    assert 'id="mp-presence-chart"' in response.text
    assert 'id="mp-booking-chart"' in response.text
    assert '"presence": []' in response.text and '"booking": []' in response.text
    assert '"chart_range":' in response.text
    network = response.text.split('data-preview-view="network"', 1)[1].split('data-preview-view="anomaly"', 1)[0]
    assert network.count('class="mp-network-graph"') == 2
    assert '기능별 서버 요청 현황' in network
    assert '게이트웨이 오류' in network and '웹 서버 5xx' in network
    assert '예매율' not in network and '동시 접속' not in network
