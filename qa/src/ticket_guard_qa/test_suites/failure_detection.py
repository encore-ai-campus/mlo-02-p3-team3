"""장애 중 탐지 · 정제 · 저장 작업 중지 → 사람 · 봇 예매 → 재시작 → 모든 세션 판정까지 시간 · 판정 누락 · 탐지율
Live = 컨테이너 중지 · 시작 제어(Docker 소켓) + 판정 읽기 연결 후 · 둘 중 하나라도 없으면 트래픽 전에 건너뜀"""
import time
from collections.abc import Callable

from ticket_guard_qa.test_runs.run_cancellation import cancellable_sleep
from ticket_guard_qa.answer_key.expected_verdicts import build_answer_key, plan_virtual_users
from ticket_guard_qa.domain.detection_targets import DETECTION_TARGETS
from ticket_guard_qa.domain.enums import SuiteOutcome
from ticket_guard_qa.domain.models import (
    FAIL_LINE_PREFIX,
    MESSAGE_LINE_SEPARATOR,
    AnswerKey,
    SuiteResult,
    SystemVerdicts,
)
from ticket_guard_qa.scoring.detection_metrics import calculate_detection_metrics
from ticket_guard_qa.system_targets.target_interfaces import PIPELINE_SERVICE_NAME, VerdictReader
from ticket_guard_qa.test_suites.failure_recovery import POLL_INTERVAL_SEC, PROCESS_WAIT_TIMEOUT_SEC, RECOVERY_TIME_LIMIT_SEC
from ticket_guard_qa.test_suites.suite_interface import SuiteContext, SuiteGuide
from ticket_guard_qa.test_suites.verdict_accuracy import send_planned_user_traffic

FAILURE_DETECTION_SUITE_NAME = "failure_detection"
METRIC_PREFIX = "failure_detection_"  # 판정 정확도 지표와 이름 분리 · 합격 기준 집계 대상 아님
MAX_LISTED_SESSION_COUNT = 3


def wait_until_judged(
    verdict_reader: VerdictReader, run_id: str, answer_key: AnswerKey, timeout_sec: float, poll_interval_sec: float,
    sleep: Callable[[float], None] = cancellable_sleep,
) -> tuple[SystemVerdicts, float]:
    """정답표 세션 전부 판정될 때까지 · (판정, 걸린 초)"""
    started_at = time.monotonic()
    while True:
        system_verdicts = verdict_reader.read_session_verdicts(run_id)
        elapsed_sec = time.monotonic() - started_at
        if answer_key.keys() <= system_verdicts.keys() or elapsed_sec >= timeout_sec:
            return system_verdicts, elapsed_sec
        sleep(poll_interval_sec)


def check_detection_rates(metrics: dict[str, float]) -> list[str]:
    """판정 정확도와 같은 합격 기준 값 · 장애 중 세션만"""
    lines = []
    for target in DETECTION_TARGETS:
        criterion = target.criterion
        value = metrics.get(criterion.metric_name)
        if value is not None and not criterion.is_met_by(value):
            lines.append(f"{FAIL_LINE_PREFIX}장애 중 {criterion.display_name} {value:.1f}% · 기준 {criterion.comparison.value} "
                         f"{criterion.threshold:g}%")
    return lines


class FailureDetectionSuite:
    suite_name = FAILURE_DETECTION_SUITE_NAME
    title = "장애 중 탐지 테스트"
    related_codes: tuple[str, ...] = ("E22",)
    guide = SuiteGuide(
        "데이터 처리 프로그램이 멈춘 동안 들어온 매크로도 복구 뒤 판정",
        "처리 중지 → 사람 · 봇 예매 → 재시작 → 모든 세션 판정까지 대기 · 정답표와 비교",
        f"{RECOVERY_TIME_LIMIT_SEC:.0f}초 이내 판정 완료 · 판정 누락 0 · 탐지율 · 오탐률 기준 동일",
    )

    def __init__(self, timeout_sec: float = PROCESS_WAIT_TIMEOUT_SEC, poll_interval_sec: float = POLL_INTERVAL_SEC) -> None:
        self.timeout_sec = timeout_sec
        self.poll_interval_sec = poll_interval_sec

    def run(self, context: SuiteContext) -> SuiteResult:
        system_target = context.system_target
        verdict_reader = system_target.verdict_reader
        process_control = system_target.process_control
        verdict_reader.read_session_verdicts(context.run_id)  # 판정 미연결 = 트래픽 전 건너뜀
        if not process_control.can_control(PIPELINE_SERVICE_NAME):
            raise NotImplementedError(f"장애 중 탐지 · {PIPELINE_SERVICE_NAME} 중지 · 시작 제어 미설정 · "
                                      "TICKET_GUARD_QA_PIPELINE_CONTAINER_NAME")
        run_config = context.run_config
        planned_users = plan_virtual_users(run_config.traffic_mix, run_config.virtual_user_count, run_config.random_seed)

        context.report_progress("정제 · 저장 작업 중지")
        process_control.stop_service(PIPELINE_SERVICE_NAME)
        try:
            context.report_progress(f"중지 중 예매 · VU {len(planned_users)}")
            session_key_by_label, _ = send_planned_user_traffic(context, planned_users, is_capture_saved=False)
        finally:
            context.report_progress("정제 · 저장 작업 재시작 · 판정 완료까지 대기")
            process_control.start_service(PIPELINE_SERVICE_NAME)

        answer_key = build_answer_key(planned_users, session_key_by_label)
        context.answer_key_repository.save_for_suite(context.run_id, self.suite_name, answer_key)  # QA DB만
        system_verdicts, elapsed_sec = wait_until_judged(
            verdict_reader, context.run_id, answer_key, self.timeout_sec, self.poll_interval_sec)
        missing_session_keys = sorted(key for key in answer_key if key not in system_verdicts)
        detection_metrics = calculate_detection_metrics(answer_key, system_verdicts)
        is_complete = not missing_session_keys

        lines = []
        if missing_session_keys:
            lines.append(f"{FAIL_LINE_PREFIX}재시작 후 판정 누락 {len(missing_session_keys)}세션 · "
                         f"{', '.join(missing_session_keys[:MAX_LISTED_SESSION_COUNT])}")
        if is_complete and elapsed_sec > RECOVERY_TIME_LIMIT_SEC:
            lines.append(f"{FAIL_LINE_PREFIX}판정 완료 {elapsed_sec:.1f}초 · 기준 {RECOVERY_TIME_LIMIT_SEC:.0f}초 초과")
        lines += check_detection_rates(detection_metrics)
        is_passed = not lines
        lines.append(f"판정 완료 {elapsed_sec:.1f}초" if is_complete else f"판정 미완료 · {self.timeout_sec:.0f}초 대기")
        lines.append(f"판정 {len(answer_key) - len(missing_session_keys)}/{len(answer_key)}세션")
        return SuiteResult(
            suite_name=self.suite_name,
            outcome=SuiteOutcome.PASSED if is_passed else SuiteOutcome.FAILED,
            metrics={
                **({f"{METRIC_PREFIX}verdict_delay_sec": elapsed_sec} if is_complete else {}),
                f"{METRIC_PREFIX}missing_verdict_count": float(len(missing_session_keys)),
                **{f"{METRIC_PREFIX}{name}": value for name, value in detection_metrics.items()},
            },
            message=MESSAGE_LINE_SEPARATOR.join(lines),
            details={"session_count": len(answer_key), "missing_session_keys": missing_session_keys[:200]},
        )
