"""Run 중지 · 진행 중 Suite 대기 · 트래픽도 바로 멈춤 · 중지 후 Run 마무리"""
import asyncio
import threading
import time

import pytest

from ticket_guard_qa.domain.enums import ActorType, RunStatus, SuiteOutcome, TargetKind
from ticket_guard_qa.domain.models import (
    RunConfig,
    SessionKey,
    VirtualUserSessionPlan,
    VirtualUserSessionResult,
)
from ticket_guard_qa.macro_bots.bot_profiles import DEFAULT_BEHAVIOR_BY_ACTOR_TYPE
from ticket_guard_qa.test_runs.run_cancellation import (
    RunCancelledError,
    bind_cancel_event,
    cancellable_sleep,
)
from ticket_guard_qa.test_runs.run_progress_stream import RunProgressStream
from ticket_guard_qa.test_runs.run_service import RunService
from ticket_guard_qa.test_suites.suite_registry import SUITE_BY_NAME
from ticket_guard_qa.traffic_generators.virtual_user_traffic import send_virtual_user_traffic

STOP_DEADLINE_SEC = 5.0


class SlowWaitSuite:
    """판정 반영 대기처럼 오래 기다리는 Suite"""
    suite_name = "slow_wait_suite"
    title = "긴 대기 Suite"
    related_codes: tuple[str, ...] = ()

    def run(self, context):
        cancellable_sleep(60)
        return None


class NeverReachedSuite:
    suite_name = "never_reached_suite"
    title = "다음 Suite"
    related_codes: tuple[str, ...] = ()
    was_run = False

    def run(self, context):
        NeverReachedSuite.was_run = True


class SlowBookingApi:
    """VU가 오래 걸리는 시스템 · 취소되면 정리 기록"""

    def __init__(self) -> None:
        self.cancelled_count = 0

    def virtual_user_capacity(self):
        return None

    async def run_virtual_user_session(self, session_plan, open_at_monotonic):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled_count += 1  # 예매 취소 정리 자리
            raise
        return VirtualUserSessionResult(SessionKey("never"), 0.0, False)


def test_sleep_stops_when_cancel_is_requested():
    cancel_event = threading.Event()
    bind_cancel_event(cancel_event)
    try:
        threading.Timer(0.1, cancel_event.set).start()
        started_at = time.monotonic()
        with pytest.raises(RunCancelledError):
            cancellable_sleep(30)
        assert time.monotonic() - started_at < STOP_DEADLINE_SEC
    finally:
        bind_cancel_event(None)


def test_sleep_outside_run_is_plain_sleep():
    cancellable_sleep(0.01)


def test_traffic_stops_and_cleans_up_when_cancel_is_requested():
    booking_api = SlowBookingApi()
    plans = [VirtualUserSessionPlan(behavior=DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.HUMAN], entry_delay_ms=0, click_intervals_ms=(), preferred_seat_ids=())
             for _ in range(3)]
    cancel_event = threading.Event()
    bind_cancel_event(cancel_event)
    try:
        threading.Timer(0.2, cancel_event.set).start()
        started_at = time.monotonic()
        with pytest.raises(RunCancelledError):
            send_virtual_user_traffic(booking_api, plans, 300.0)
        assert time.monotonic() - started_at < STOP_DEADLINE_SEC
    finally:
        bind_cancel_event(None)
    assert booking_api.cancelled_count == 3  # VU마다 정리 실행


def test_cancel_stops_run_in_the_middle_of_a_suite(qa_settings, qa_database, monkeypatch):
    monkeypatch.setitem(SUITE_BY_NAME, SlowWaitSuite.suite_name, SlowWaitSuite())
    monkeypatch.setitem(SUITE_BY_NAME, NeverReachedSuite.suite_name, NeverReachedSuite())
    progress_stream = RunProgressStream()
    run_service = RunService(qa_settings, qa_database, progress_stream)
    run_id = run_service.start_in_background(
        RunConfig(TargetKind.FAKE, (SlowWaitSuite.suite_name, NeverReachedSuite.suite_name)))
    deadline = time.monotonic() + STOP_DEADLINE_SEC
    while not any(m.text.startswith("긴 대기") for m in progress_stream.read_after(run_id, 0)):
        assert time.monotonic() < deadline
        time.sleep(0.02)

    run_service.cancel(run_id)
    while run_service.active_run_id is not None:
        assert time.monotonic() < deadline, "중지 후에도 Run이 끝나지 않음"
        time.sleep(0.02)

    summary = run_service.run_repository.get_summary(run_id)
    assert summary.status is RunStatus.CANCELLED
    assert [result.outcome for result in summary.suite_results] == [SuiteOutcome.SKIPPED]
    assert not NeverReachedSuite.was_run
    assert progress_stream.read_after(run_id, 0)[-1].final_status == "CANCELLED"
