"""판정 정확도 · 탐지율 · 오탐률 · 기획서 21쪽 · 탐지 = CHALLENGE 이상"""
import time
from collections import Counter
from collections.abc import Callable

from ticket_guard_qa.test_runs.run_cancellation import cancellable_sleep
from ticket_guard_qa.answer_key.expected_verdicts import build_answer_key, plan_virtual_users
from ticket_guard_qa.domain.enums import SessionStep, SuiteOutcome, TargetKind
from ticket_guard_qa.domain.models import (
    MESSAGE_LINE_SEPARATOR,
    AnswerKey,
    PlannedUser,
    SessionKey,
    SuiteResult,
    SystemVerdicts,
    VirtualUserSessionResult,
)
from ticket_guard_qa.macro_bots.bot_profiles import BotProfileRegistry
from ticket_guard_qa.scoring.confusion_matrix import build_confusion_matrix
from ticket_guard_qa.system_targets.target_interfaces import BookingApi
from ticket_guard_qa.scoring.detection_breakdown import count_rule_hits
from ticket_guard_qa.scoring.detection_metrics import (
    calculate_detection_metrics,
    calculate_unlearned_metrics,
    find_false_positives,
    find_missed_bots,
)
from ticket_guard_qa.scoring.latency_metrics import calculate_error_rate_percent, calculate_p95
from ticket_guard_qa.test_suites.suite_interface import SuiteContext, SuiteGuide
from ticket_guard_qa.test_suites.verdict_collection import (
    collect_verdicts,
    describe_blocked_sessions,
    describe_verdict_collection,
    select_judged_answer_key,
)
from ticket_guard_qa.traffic_generators.session_funnel import build_actor_progress, build_funnel_progress
from ticket_guard_qa.traffic_generators.virtual_user_traffic import (
    build_planned_user_session_plans,
    save_browser_captures,
    reset_before_traffic,
    send_virtual_user_traffic,
)

VERDICT_ACCURACY_SUITE_NAME = "verdict_accuracy"  # 04 · B1 · B4가 결과를 찾는 이름
MAX_LISTED_SESSION_COUNT = 200  # 04 · 05 화면 목록 길이
LIVE_VERDICT_SETTLE_SEC = 30.0  # Live · Kafka → Spark 처리 주기 5초 · 이상 탐지 갱신 · 판정 반영 여유


def send_planned_user_traffic(
    context: SuiteContext, planned_users: list[PlannedUser], booking_api: BookingApi | None = None,
    bot_profile_registry: BotProfileRegistry | None = None, random_seed: int | None = None, is_capture_saved: bool = True,
) -> tuple[dict[str, SessionKey], list[VirtualUserSessionResult]]:
    """가상 사용자 라벨 → 시스템이 준 세션 키 · 시스템에는 행동 값만 보냄 · VU 동시 실행
    탐지 회귀 = 대상 · 봇 버전 · 시드 고정해서 호출 · 나머지 = Run 설정 그대로"""
    booking_api = booking_api or context.system_target.booking_api
    context.report_progress(reset_before_traffic(booking_api))

    def report_traffic_progress(results: list[VirtualUserSessionResult | None]) -> None:
        finished_count = sum(result is not None for result in results)
        context.report_progress(
            f"Entered {finished_count}/{len(planned_users)}",
            actor_progress=build_funnel_progress(planned_users, results),
        )

    session_results = send_virtual_user_traffic(
        booking_api,
        build_planned_user_session_plans(planned_users, bot_profile_registry or context.bot_profile_registry,
                                         context.run_config.random_seed if random_seed is None else random_seed,
                                         context.run_config.wave_size),
        context.traffic_time_limit_sec, report_traffic_progress, context.run_config.wave_size,
    )
    if is_capture_saved:  # B3 브라우저 화면 · 판정 정확도 Run만
        save_browser_captures(context.report_output_dir, context.run_id, planned_users, session_results)
    session_key_by_label = {
        user.virtual_user_label: result.session_key for user, result in zip(planned_users, session_results)
    }
    return session_key_by_label, session_results


def build_result_details(answer_key: AnswerKey, system_verdicts: SystemVerdicts) -> dict:
    missed_bots = find_missed_bots(answer_key, system_verdicts)
    false_positives = find_false_positives(answer_key, system_verdicts)
    confusion_matrix = build_confusion_matrix(answer_key, system_verdicts)
    human_session_count = sum(1 for actor_type in answer_key.values() if not actor_type.is_bot)
    return {
        "session_count": len(answer_key),
        "human_session_count": human_session_count,
        "bot_session_count": len(answer_key) - human_session_count,
        "missed_bot_count": len(missed_bots),
        "false_positive_count": len(false_positives),
        "missed_bot_session_keys": missed_bots[:MAX_LISTED_SESSION_COUNT],
        "false_positive_session_keys": false_positives[:MAX_LISTED_SESSION_COUNT],
        "confusion_matrix": {
            actor_type.value: {stage.value: count for stage, count in row.items()}
            for actor_type, row in confusion_matrix.items()
        },
    }


def read_rule_hit_counts_or_none(context: SuiteContext) -> dict[str, int] | None:
    """SESSION_VERDICTS.matchedRules · 시스템이 아직 판정 이유를 안 주면 None · B4 룰 메타데이터 미수신 표시"""
    try:
        return count_rule_hits(context.system_target.verdict_reader.read_verdict_records(context.run_id))
    except NotImplementedError:
        return None


class VerdictAccuracySuite:
    suite_name = VERDICT_ACCURACY_SUITE_NAME
    title = "판정 정확도"
    related_codes: tuple[str, ...] = ("E22",)  # 보류 판정
    guide = SuiteGuide(
        "시스템이 매크로는 매크로로, 사람은 사람으로 판정",
        "누가 봇인지 적은 정답표와 시스템 판정 비교",
        "매크로 탐지 90% 이상(브라우저 자동화 70%) · 사람 오판 1% 이하",
    )

    def __init__(self, live_settle_sec: float = LIVE_VERDICT_SETTLE_SEC, sleep: Callable[[float], None] = cancellable_sleep) -> None:
        self.live_settle_sec = live_settle_sec
        self.sleep = sleep

    def run(self, context: SuiteContext) -> SuiteResult:
        run_config = context.run_config
        # 판정 읽기 미연결(NotImplementedError)이면 트래픽 전에 건너뜀 · 연결되면 그대로 진행 · 약 5분 절약
        context.system_target.verdict_reader.read_session_verdicts(context.run_id)
        planned_users = plan_virtual_users(run_config.traffic_mix, run_config.virtual_user_count, run_config.random_seed)
        context.report_progress(f"VU {len(planned_users)} 접속")
        session_key_by_label, session_results = send_planned_user_traffic(context, planned_users)

        answer_key = build_answer_key(planned_users, session_key_by_label)
        context.answer_key_repository.save(context.run_id, answer_key)  # QA DB만
        context.report_progress(f"Ground Truth 저장 · {len(answer_key)}세션")

        is_live = context.system_target.kind is TargetKind.LIVE
        if is_live:
            context.report_progress(f"판정 반영 대기 · {self.live_settle_sec:.0f}초")
            self.sleep(self.live_settle_sec)  # 트래픽 끝 → 파이프라인 판정 저장까지
        collection = collect_verdicts(
            lambda: context.system_target.verdict_reader.read_session_verdicts(context.run_id),
            select_judged_answer_key(answer_key, session_results), is_live, context.report_progress, self.sleep,
        )
        judged_key, system_verdicts = collection.judged_answer_key, collection.verdicts
        rule_hit_count_by_rule_id = read_rule_hit_counts_or_none(context)
        context.report_progress(f"Verdict {collection.received_count}/{collection.judged_count}", actor_progress=build_actor_progress(planned_users, {
            SessionStep.ENTERED: Counter(answer_key.values()),
            SessionStep.VERDICT_RECEIVED: Counter(judged_key[key] for key in judged_key if key in system_verdicts),
        }))
        # 판정 API 응답 = 세션 시작 응답 · 끝까지 진행한 세션만 · 즉시 차단은 오류율에서 제외 · 대상 0 = 측정 불가(값 없음)
        latency_values_sec = [result.api_response_latency_ms / 1000 for result in session_results if result.is_completed]
        judged_results = [result for result in session_results if not result.is_excluded]
        error_count = sum(1 for result in judged_results if result.is_error)
        metrics = {
            **(calculate_detection_metrics(judged_key, system_verdicts) if collection.is_measurable else {}),
            **(calculate_unlearned_metrics(judged_key, system_verdicts) if collection.is_measurable else {}),
            **({"api_latency_p95_sec": calculate_p95(latency_values_sec)} if latency_values_sec else {}),
            **({"api_error_rate_percent": calculate_error_rate_percent(len(judged_results), error_count)} if judged_results else {}),
        }
        blocked_line = describe_blocked_sessions(answer_key, session_results)
        return SuiteResult(
            suite_name=self.suite_name,
            # 판정 수신율 기준 미만 = 탐지율 · 오탐률 측정 불가 → SKIPPED · 탐지 회귀와 같은 표시
            outcome=SuiteOutcome.PASSED if collection.is_measurable else SuiteOutcome.SKIPPED,
            metrics=metrics,
            message=MESSAGE_LINE_SEPARATOR.join([describe_verdict_collection(collection), *([blocked_line] if blocked_line else [])]),
            details={**build_result_details(judged_key, system_verdicts), "rule_hit_count_by_rule_id": rule_hit_count_by_rule_id,
                     "verdict_received_count": collection.received_count, "is_verdict_measurable": collection.is_measurable},
        )
