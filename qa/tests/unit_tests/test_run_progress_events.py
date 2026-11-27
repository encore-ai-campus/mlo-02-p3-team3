"""03 · B3 진행 메시지 · 끊긴 지점부터 이어 받기 · 실시간 지표 조회 실패에도 경과 시간 유지"""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from pymongo.errors import ServerSelectionTimeoutError  # noqa: E402

from ticket_guard_qa.dashboard.dashboard_app import create_dashboard_app  # noqa: E402
from ticket_guard_qa.dashboard.dependencies import ADMIN_USER_HEADER  # noqa: E402
from ticket_guard_qa.dashboard.routes.test_run_routes import (  # noqa: E402
    format_server_sent_event,
    read_resume_sequence_number,
)
from ticket_guard_qa.domain.enums import RunStatus, TargetKind  # noqa: E402
from ticket_guard_qa.domain.models import RunConfig  # noqa: E402
from ticket_guard_qa.system_targets.fake_system import build_fake_system_target  # noqa: E402
from ticket_guard_qa.test_runs.run_progress_stream import ProgressMessage  # noqa: E402
from ticket_guard_qa.test_runs.run_service import read_live_metrics_or_none  # noqa: E402

RUN_ID = "run-events"


def read_event_ids(body: str) -> list[int]:
    return [int(line.removeprefix("id: ")) for line in body.splitlines() if line.startswith("id: ")]


@pytest.fixture
def client_and_stream(qa_settings):
    app = create_dashboard_app(qa_settings)
    progress_stream = app.state.progress_stream
    app.state.run_service.run_repository.create_run(RUN_ID, RunConfig(TargetKind.FAKE, ("self_check",)), datetime.now(timezone.utc))
    for text in ("시험 데이터 초기화", "VU 10 접속", "판정 반영 대기"):
        progress_stream.publish(RUN_ID, text)
    progress_stream.publish(RUN_ID, "Run 종료 · PASSED", is_finished=True)
    return TestClient(app, headers={ADMIN_USER_HEADER: "qa-admin"}), progress_stream


def test_event_carries_sequence_number_as_id():
    event = format_server_sent_event(ProgressMessage(7, "측정 중"))
    assert event.startswith("id: 7\ndata: ")
    assert event.endswith("\n\n")


@pytest.mark.parametrize(("last_event_id", "after", "expected"), [
    (None, 0, 0), ("5", 0, 5), (None, 3, 3), ("5", 8, 8), ("wrong", 2, 2), (None, -1, 0),
])
def test_resume_number_takes_latest_of_header_and_query(last_event_id, after, expected):
    assert read_resume_sequence_number(last_event_id, after) == expected


def test_stream_sends_every_message_with_ids(client_and_stream):
    client, _ = client_and_stream
    body = client.get(f"/runs/{RUN_ID}/events").text
    assert body.startswith("retry: ")
    assert read_event_ids(body) == [1, 2, 3, 4]


def test_browser_reconnect_resumes_after_last_event_id(client_and_stream):
    client, _ = client_and_stream
    body = client.get(f"/runs/{RUN_ID}/events", headers={"Last-Event-ID": "2"}).text
    assert read_event_ids(body) == [3, 4]
    assert "VU 10 접속" not in body


def test_screen_reconnect_resumes_after_query_number(client_and_stream):
    client, _ = client_and_stream
    assert read_event_ids(client.get(f"/runs/{RUN_ID}/events?after=3").text) == [4]


class FailingLiveMetricsReader:
    def read_live_metrics(self, run_id, elapsed_sec):
        raise ServerSelectionTimeoutError("system mongo timeout")


def test_live_metrics_failure_keeps_progress_ticking():
    system_target = replace(build_fake_system_target(), live_metrics_reader=FailingLiveMetricsReader())
    assert read_live_metrics_or_none(system_target, RUN_ID, 3.0) is None


def test_stream_of_unknown_run_is_not_found(client_and_stream):
    client, _ = client_and_stream
    assert client.get("/runs/run-unknown/events").status_code == 404


def test_stream_closes_with_saved_status_when_finish_signal_is_lost(qa_settings):
    """프로그램 재시작 뒤 03 화면 · 종료 신호가 메모리에서 사라져도 DB 최종 상태로 마무리"""
    app = create_dashboard_app(qa_settings)
    repository = app.state.run_service.run_repository
    repository.create_run("run-lost", RunConfig(TargetKind.FAKE, ("self_check",)), datetime.now(timezone.utc))
    app.state.run_service.run_repository.runs.update_one({"_id": "run-lost"}, {"$set": {"status": RunStatus.ERROR.value}})
    client = TestClient(app, headers={ADMIN_USER_HEADER: "qa-admin"})
    body = client.get("/runs/run-lost/events").text
    assert '"is_finished": true' in body and '"final_status": "ERROR"' in body
