"""예매 흐름 · VU 동시 접속 → 대기열 → 보안문자 → 좌석 클릭 → 확정 · 단계별 응답 시간 · 중복 판매 · 1-b ~ 1-d"""
from collections import Counter

from ticket_guard_qa.answer_key.expected_verdicts import plan_virtual_users
from ticket_guard_qa.domain.enums import BookingOutcome, SuiteOutcome
from ticket_guard_qa.domain.models import (
    MESSAGE_LINE_SEPARATOR,
    REFERENCE_LINE_PREFIX,
    SuiteResult,
    VirtualUserSessionResult,
)
from ticket_guard_qa.scoring.latency_metrics import calculate_p95
from ticket_guard_qa.system_targets.target_interfaces import AccountPoolExhaustedError
from ticket_guard_qa.test_suites.suite_interface import SuiteContext, SuiteGuide
from ticket_guard_qa.traffic_generators.session_funnel import ACTOR_PROGRESS_DETAIL_KEY, build_funnel_progress
from ticket_guard_qa.traffic_generators.virtual_user_traffic import (
    build_planned_user_session_plans,
    save_browser_captures,
    reset_before_traffic,
    send_virtual_user_traffic,
)

BOOKING_FLOW_SUITE_NAME = "booking_flow"
STEP_TITLES = {
    "login": "로그인", "start": "예매 시작", "queue": "대기열", "captcha": "보안문자",
    "seat_select": "좌석 클릭", "confirm": "확정",
}


def summarize_step_p95(session_results: list[VirtualUserSessionResult]) -> dict[str, float]:
    step_values: dict[str, list[float]] = {}
    for result in session_results:
        for step, latency_ms in result.step_latency_ms.items():
            step_values.setdefault(step, []).append(latency_ms)
    return {step: calculate_p95(values) for step, values in step_values.items()}


def find_double_sold_seat_ids(session_results: list[VirtualUserSessionResult]) -> list[str]:
    seat_counts = Counter(seat for result in session_results for seat in result.booked_seat_ids)
    return sorted(seat for seat, count in seat_counts.items() if count > 1)


def count_outcomes(session_results: list[VirtualUserSessionResult | None]) -> Counter:
    return Counter(result.booking_outcome for result in session_results if result and not result.is_error)


def build_blocked_line(session_results: list[VirtualUserSessionResult]) -> str | None:
    """즉시 차단 = 서버 오류 아님 · 시험 계정 구매 한도 · 일시 제한 · 참고 줄"""
    blocked_counts = Counter(result.failed_step or "unknown" for result in session_results if result.is_blocked)
    if not blocked_counts:
        return None
    listed = ", ".join(f"{step} {count}건" for step, count in blocked_counts.most_common())
    return f"{REFERENCE_LINE_PREFIX}시스템 즉시 차단 {sum(blocked_counts.values())}건 · {listed} · 서버 오류 아님"


def build_dropped_line(session_results: list[VirtualUserSessionResult]) -> str | None:
    """브라우저 봇 탈락 = 예매 시작 전 응답 없음 · QA 실행 자원 문제 · 서버 오류 아님 · 참고 줄"""
    dropped_counts = Counter(result.failed_step or "unknown" for result in session_results if result.is_dropped)
    if not dropped_counts:
        return None
    listed = ", ".join(f"{step} {count}건" for step, count in dropped_counts.most_common())
    return f"{REFERENCE_LINE_PREFIX}브라우저 봇 탈락 {sum(dropped_counts.values())}명 · {listed} · QA 실행 자원 · 서버 오류 아님"


def build_message(session_results: list[VirtualUserSessionResult], planned_count: int) -> str:
    error_count = sum(1 for result in session_results if result.is_error)
    completed = [result for result in session_results if result.is_completed]
    outcome_counts = count_outcomes(session_results)
    blocked_text = f" · 차단 {outcome_counts[BookingOutcome.BLOCKED]}" if outcome_counts[BookingOutcome.BLOCKED] else ""
    dropped_text = f" · 탈락 {outcome_counts[BookingOutcome.DROPPED]}" if outcome_counts[BookingOutcome.DROPPED] else ""
    parts = [
        f"예매 완료 {outcome_counts[BookingOutcome.BOOKED]} · 매진 {outcome_counts[BookingOutcome.SOLD_OUT]}{blocked_text}{dropped_text}"
        f" · 오류 {error_count} / VU {planned_count}"
    ]
    if completed:
        parts.append(f"전체 p95 {calculate_p95([result.latency_ms for result in completed]):.0f}ms")
        parts.extend(f"{STEP_TITLES.get(step, step)} {value:.0f}ms" for step, value in summarize_step_p95(completed).items())
    fail_lines = []  # 실패 원인은 줄을 나눔 · 04 결론 · 원문 가독성
    double_sold = find_double_sold_seat_ids(session_results)
    if double_sold:
        fail_lines.append(f"FAIL: 중복 판매 {len(double_sold)}석 ({', '.join(double_sold[:5])})")
    failure_counts = Counter(result.failed_step or "unknown" for result in session_results if result.is_error)
    if failure_counts:
        fail_lines.append("FAIL: " + ", ".join(f"{step} {count}건" for step, count in failure_counts.most_common()))
    reference_lines = [line for line in (build_blocked_line(session_results), build_dropped_line(session_results)) if line]
    return MESSAGE_LINE_SEPARATOR.join([*fail_lines, " · ".join(parts), *reference_lines])


class BookingFlowSuite:
    suite_name = BOOKING_FLOW_SUITE_NAME
    title = "예매 흐름 테스트"
    related_codes: tuple[str, ...] = ("E05", "E12")
    guide = SuiteGuide(
        "많은 사람이 동시에 예매할 때 끝까지 정상 진행",
        "가상 사용자가 로그인 → 대기열 → 보안문자 → 좌석 선택 → 예매 확정 진행",
        "오류 0 · 같은 좌석 두 번 판매 0",
    )

    def run(self, context: SuiteContext) -> SuiteResult:
        run_config = context.run_config
        booking_api = context.system_target.booking_api
        planned_users = plan_virtual_users(run_config.traffic_mix, run_config.virtual_user_count, run_config.random_seed)
        planned_count = len(planned_users)

        reset_message = reset_before_traffic(booking_api)
        context.report_progress(reset_message)
        context.report_progress(f"VU {planned_count} 동시 접속 · 오픈 전 로그인")

        def report_traffic_progress(results: list[VirtualUserSessionResult | None]) -> None:
            outcome_counts = count_outcomes(results)
            finished_count = sum(result is not None for result in results)
            context.report_progress(
                f"VU {finished_count}/{planned_count} 종료 · 예매 완료 {outcome_counts[BookingOutcome.BOOKED]}"
                f" · 매진 {outcome_counts[BookingOutcome.SOLD_OUT]}",
                actor_progress=build_funnel_progress(planned_users, results),
            )

        try:
            session_results = send_virtual_user_traffic(
                booking_api,
                build_planned_user_session_plans(planned_users, context.bot_profile_registry, run_config.random_seed, run_config.wave_size),
                context.traffic_time_limit_sec, report_traffic_progress, run_config.wave_size,
            )
            save_browser_captures(context.report_output_dir, context.run_id, planned_users, session_results)  # B3 브라우저 화면
        except AccountPoolExhaustedError as error:
            return SuiteResult(self.suite_name, SuiteOutcome.FAILED, message=f"FAIL: {error}")

        error_count = sum(1 for result in session_results if result.is_error)
        succeeded_latencies = [result.latency_ms for result in session_results if result.is_completed]
        outcome_counts = count_outcomes(session_results)
        double_sold = find_double_sold_seat_ids(session_results)
        is_passed = error_count == 0 and not double_sold  # 즉시 차단 · 브라우저 봇 탈락 = 오류 아님 · 참고 줄
        return SuiteResult(
            suite_name=self.suite_name,
            outcome=SuiteOutcome.PASSED if is_passed else SuiteOutcome.FAILED,
            metrics={
                "booking_flow_success_count": float(len(succeeded_latencies)),
                "booking_flow_failure_count": float(error_count),
                "booking_flow_blocked_count": float(outcome_counts[BookingOutcome.BLOCKED]),
                "booking_flow_dropped_count": float(outcome_counts[BookingOutcome.DROPPED]),
                "booking_flow_booked_count": float(outcome_counts[BookingOutcome.BOOKED]),
                "booking_flow_sold_out_count": float(outcome_counts[BookingOutcome.SOLD_OUT]),
                "booking_flow_double_sold_seat_count": float(len(double_sold)),
                **({"booking_flow_p95_latency_ms": calculate_p95(succeeded_latencies)} if succeeded_latencies else {}),
            },
            message=f"{build_message(session_results, planned_count)} · {reset_message}",
            details={"step_p95_latency_ms": summarize_step_p95([r for r in session_results if r.is_completed]),
                     "double_sold_seat_ids": double_sold,
                     ACTOR_PROGRESS_DETAIL_KEY: build_funnel_progress(planned_users, session_results)},
        )
