"""EC2 Live 오판 방지 · 계정 한도 · 차단 만료 · 판정 수신율 · 늦게 오는 서버 이벤트 · 부하 차단 분류 · 차단 = 응답 시간 · 오류율 제외"""
from dataclasses import dataclass, field

from ticket_guard_qa.domain.enums import ActorType, BookingOutcome, VerdictStage
from ticket_guard_qa.domain.models import SessionKey, VirtualUserSessionResult
from ticket_guard_qa.system_targets.target_interfaces import AdmissionSample, StreamRecord
from ticket_guard_qa.system_targets.test_account_ledger import (
    POLICY_BLOCK_SEC,
    SALE_CYCLE_SEC,
    TestAccountLedger,
)
from ticket_guard_qa.test_suites.booking_flow import build_message as build_booking_message
from ticket_guard_qa.test_suites.event_delivery import (
    BOOKED_SERVER_EVENT_TYPES,
    LATE_SERVER_EVENT_WAIT_SEC,
    evaluate_delivery,
    wait_for_late_server_events,
)
from ticket_guard_qa.test_suites.load_capacity import LoadReport
from ticket_guard_qa.test_suites.verdict_collection import (
    collect_verdicts,
    describe_verdict_collection,
    select_judged_answer_key,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def session(key: str, outcome: BookingOutcome | None, is_error: bool = False, failed_step: str | None = None,
            latency_ms: float = 500.0) -> VirtualUserSessionResult:
    return VirtualUserSessionResult(SessionKey(key), latency_ms, is_error, failed_step=failed_step, booking_outcome=outcome)


def test_ledger_rotates_and_releases_limits_after_cycle_and_block():
    clock = FakeClock()
    ledger = TestAccountLedger(3, clock)
    assert [ledger.take_usable(set(), 2) for _ in range(4)] == [1, 2, 3, 1]  # 순환
    ledger.record_purchase(1, 4)
    ledger.record_block(2)
    assert ledger.take_usable(set(), 2) == 3 and ledger.count_usable(set(), 2) == 1
    clock.now += POLICY_BLOCK_SEC  # 즉시 차단 5분 지남
    assert ledger.count_usable(set(), 2) == 2
    clock.now += SALE_CYCLE_SEC  # 판매 회차 지남 · 구매 한도 다시
    assert ledger.count_usable(set(), 2) == 3


def test_blocked_sessions_leave_errors_and_latency_alone():
    results = [session("a1", BookingOutcome.BOOKED), session("failed-qa-user-0002", BookingOutcome.BLOCKED,
                                                                   failed_step="start · TEMPORARILY_RESTRICTED", latency_ms=0.0)]
    message = build_booking_message(results, 2)
    assert "차단 1 · 오류 0 / VU 2" in message and "전체 p95 500ms" in message
    assert "참고: 시스템 즉시 차단 1건 · start · TEMPORARILY_RESTRICTED 1건 · 서버 오류 아님" in message


def test_missing_verdicts_make_detection_unmeasurable_after_waiting():
    clock = FakeClock()
    answer_key = {SessionKey("a1"): ActorType.HUMAN, SessionKey("a2"): ActorType.BOT_A, SessionKey("failed-qa-user-0003"): ActorType.HUMAN}
    results = [session("a1", BookingOutcome.BOOKED), session("a2", BookingOutcome.SOLD_OUT),
               session("failed-qa-user-0003", None, is_error=True)]
    judged = select_judged_answer_key(answer_key, results)
    assert set(judged) == {"a1", "a2"}  # QA가 만든 키 · 오류 세션 제외

    collection = collect_verdicts(lambda: {}, judged, True, lambda text: None, clock.sleep, clock, wait_sec=30.0, poll_sec=5.0)
    assert not collection.is_measurable and collection.waited_sec >= 30.0
    assert "판정 수신 0/2 (0%) · 기준 90% 미만 · 탐지율 · 오탐률 측정 불가" in describe_verdict_collection(collection)


def test_verdicts_arriving_late_are_collected():
    clock = FakeClock()
    judged = {SessionKey("a1"): ActorType.HUMAN, SessionKey("a2"): ActorType.BOT_A}
    responses = iter([{}, {SessionKey("a1"): VerdictStage.NORMAL, SessionKey("a2"): VerdictStage.HOLD}])
    collection = collect_verdicts(lambda: next(responses), judged, True, lambda text: None, clock.sleep, clock)
    assert collection.is_measurable and collection.received_count == 2


@dataclass
class ProgressContext:
    run_id: str = "run-late"
    messages: list[str] = field(default_factory=list)

    def report_progress(self, text: str, **fields) -> None:
        self.messages.append(text)


class LateStreamReader:
    """두 번째 읽기에서 서버 이벤트 도착 · outbox 비동기 발행 흉내"""

    def __init__(self, late_records: list[StreamRecord]) -> None:
        self.late_records = late_records
        self.read_count = 0

    def read_since(self, start_offsets, run_id):
        self.read_count += 1
        return self.late_records


def build_server_records(attempt_id: str) -> list[StreamRecord]:
    return [StreamRecord(0, index, attempt_id, None, {"eventId": f"{attempt_id}-{event_type}", "eventType": event_type,
                                                       "userId": "qa-user-0001", "bookingAttemptId": attempt_id})
            for index, event_type in enumerate(sorted(BOOKED_SERVER_EVENT_TYPES))]


def test_late_server_events_are_waited_for_instead_of_missing():
    clock = FakeClock()
    results = [session("a1", BookingOutcome.BOOKED)]
    first_report = evaluate_delivery(results, [])
    assert len(first_report.missing_server_events) == len(BOOKED_SERVER_EVENT_TYPES)

    reader = LateStreamReader(build_server_records("a1"))
    report, records, waited_sec = wait_for_late_server_events(
        ProgressContext(), reader, {0: 0}, results, first_report, [], clock.sleep, clock)
    assert report.missing_server_events == [] and reader.read_count == 1 and 0 < waited_sec < LATE_SERVER_EVENT_WAIT_SEC


def test_load_policy_blocks_are_not_server_errors():
    report = LoadReport(2, samples=[AdmissionSample(None, None, 0.0, "start · TEMPORARILY_RESTRICTED"),
                                    AdmissionSample(None, None, 0.0, "connection · ReadTimeout")])
    assert report.server_error_count == 1 and report.blocked_errors == ["start · TEMPORARILY_RESTRICTED"]
