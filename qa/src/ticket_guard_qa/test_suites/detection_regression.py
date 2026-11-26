"""탐지 회귀 · 규칙이 바뀔 때마다 같은 시드 · 같은 봇(기본 v1) · 같은 트래픽으로 판정 → 기준선과 유형별 탐지율 비교
기준선 = case_data/regression_baselines/{대상}.json · 숫자만(정답표 · 봇 설정 없음) · Mock = 매번 새 가짜 대상"""
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ticket_guard_qa.answer_key.expected_verdicts import build_answer_key, plan_virtual_users
from ticket_guard_qa.domain.enums import ActorType, SuiteOutcome, TargetKind
from ticket_guard_qa.domain.models import (
    FAIL_LINE_PREFIX,
    MESSAGE_LINE_SEPARATOR,
    REFERENCE_LINE_PREFIX,
    SuiteResult,
    TrafficMix,
)
from ticket_guard_qa.macro_bots.bot_catalog import BotRole, get_bot_type_info
from ticket_guard_qa.macro_bots.bot_profiles import DEFAULT_BEHAVIOR_BY_ACTOR_TYPE, BotProfile, BotProfileRegistry
from ticket_guard_qa.scoring.detection_metrics import (
    calculate_detection_rate_by_actor,
    calculate_unlearned_metrics,
    list_evaluation_actor_types,
)
from ticket_guard_qa.system_targets.fake_system import build_fake_system_target
from ticket_guard_qa.test_suites.suite_interface import SuiteContext, SuiteGuide
from ticket_guard_qa.test_suites.verdict_accuracy import LIVE_VERDICT_SETTLE_SEC, send_planned_user_traffic
from ticket_guard_qa.test_suites.verdict_collection import (
    collect_verdicts,
    describe_blocked_sessions,
    describe_verdict_collection,
    select_judged_answer_key,
)

DETECTION_REGRESSION_SUITE_NAME = "detection_regression"
REGRESSION_RANDOM_SEED = 20261120  # Run 설정 시드와 무관 · 바꾸면 기준선 다시 저장
REGRESSION_VIRTUAL_USER_COUNT = 200
REGRESSION_TRAFFIC_PERCENTS: dict[str, int] = {  # 기본 유형 전부 · 사람 53 · 봇 7종 5% · 미학습 4종 3%
    ActorType.HUMAN.value: 53,
    **{actor_type.value: 5 for actor_type in (
        ActorType.BOT_A, ActorType.BOT_B, ActorType.BOT_C, ActorType.BOT_D,
        ActorType.BOT_CAPTCHA, ActorType.BOT_REENTRY, ActorType.BOT_DEVICE_SPOOF,
    )},
    **{actor_type.value: 3 for actor_type in (
        ActorType.BOT_NEW, ActorType.BOT_NEW_SLOW_REGULAR, ActorType.BOT_NEW_SPLIT_ACCOUNT, ActorType.BOT_NEW_HUMAN_MIMIC,
    )},
}
ALLOWED_CHANGE_PERCENT_POINT = 5.0  # 탐지율 하락 · 오탐률 상승 허용 폭 · 세션 1개 = 5 ~ 10%p
BASELINE_FOLDER_NAME = "regression_baselines"


@dataclass(frozen=True)
class RegressionBaseline:
    """유형별 탐지율 · 사람 = 오탐률 · 같은 시드 · VU 수 · 트래픽일 때만 비교"""
    rule_set_version: str | None
    random_seed: int
    virtual_user_count: int
    traffic_percents: dict[str, int]
    detection_rate_percent_by_actor: dict[str, float] = field(default_factory=dict)


def find_baseline_path(case_data_dir: Path, target_kind: TargetKind) -> Path:
    return case_data_dir / BASELINE_FOLDER_NAME / f"{target_kind.value}.json"


def load_baseline(case_data_dir: Path, target_kind: TargetKind) -> RegressionBaseline | None:
    path = find_baseline_path(case_data_dir, target_kind)
    if not path.exists():
        return None
    return RegressionBaseline(**json.loads(path.read_text(encoding="utf-8")))


def save_baseline(case_data_dir: Path, target_kind: TargetKind, baseline: RegressionBaseline) -> Path:
    """의도한 규칙 변경 뒤에만 · ticket-guard-qa save-regression-baseline"""
    path = find_baseline_path(case_data_dir, target_kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(baseline), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_regression_registry() -> BotProfileRegistry:
    """기본 유형 v1 고정 · B2 저장 버전 · 추가한 유형 영향 없음 · 판정 규칙 변화만 보이게"""
    return BotProfileRegistry(tuple(
        BotProfile(ActorType(actor_id), DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType(actor_id)], change_note="회귀 고정")
        for actor_id in REGRESSION_TRAFFIC_PERCENTS
    ))


def format_actor_name(actor_id: str) -> str:
    """봇 A · 정상 · 미학습 2"""
    info = get_bot_type_info(actor_id)
    return info.role_label if info.role is BotRole.DETECTION_TARGET else info.short_name


def is_same_condition(baseline: RegressionBaseline) -> bool:
    return (baseline.random_seed, baseline.virtual_user_count, baseline.traffic_percents) == (
        REGRESSION_RANDOM_SEED, REGRESSION_VIRTUAL_USER_COUNT, REGRESSION_TRAFFIC_PERCENTS)


def compare_with_baseline(measured: dict[str, float], baseline: RegressionBaseline) -> tuple[list[str], list[str]]:
    """(FAIL 줄, 참고 줄) · 봇 = 기준선보다 허용 폭 넘게 하락 · 사람 = 허용 폭 넘게 상승"""
    fail_lines, reference_lines = [], []
    for actor_id, rate in measured.items():
        base = baseline.detection_rate_percent_by_actor.get(actor_id)
        name = format_actor_name(actor_id)
        if base is None:
            reference_lines.append(f"{REFERENCE_LINE_PREFIX}{name} 기준선 없음 · 이번 {rate:.1f}%")
        elif actor_id == ActorType.HUMAN.value and rate > base + ALLOWED_CHANGE_PERCENT_POINT:
            fail_lines.append(f"{FAIL_LINE_PREFIX}정상 오탐률 상승 · {base:.1f}% → {rate:.1f}%")
        elif actor_id != ActorType.HUMAN.value and rate < base - ALLOWED_CHANGE_PERCENT_POINT:
            fail_lines.append(f"{FAIL_LINE_PREFIX}{name} 탐지율 하락 · {base:.1f}% → {rate:.1f}%")
    for actor_id in baseline.detection_rate_percent_by_actor.keys() - measured.keys():
        reference_lines.append(f"{REFERENCE_LINE_PREFIX}{format_actor_name(actor_id)} 이번 Run 세션 없음")
    return fail_lines, reference_lines


def read_rule_set_version(context: SuiteContext, verdict_reader) -> str | None:
    try:
        records = verdict_reader.read_verdict_records(context.run_id)
    except NotImplementedError:
        return None
    return records[0].rule_set_version if records else None


class DetectionRegressionSuite:
    suite_name = DETECTION_REGRESSION_SUITE_NAME
    title = "탐지 회귀 테스트"
    related_codes: tuple[str, ...] = ("E22",)
    guide = SuiteGuide(
        "판정 규칙이 바뀌어도 이전에 잡던 매크로를 계속 탐지",
        "같은 시드 · 같은 봇(기본값) · 같은 트래픽으로 판정 → 기준선과 유형별 탐지율 비교",
        f"봇별 탐지율 하락 · 정상 오탐률 상승 {ALLOWED_CHANGE_PERCENT_POINT:.0f}%p 이내",
    )

    def __init__(self, live_settle_sec: float = LIVE_VERDICT_SETTLE_SEC, sleep: Callable[[float], None] = time.sleep) -> None:
        self.live_settle_sec = live_settle_sec
        self.sleep = sleep

    def run(self, context: SuiteContext) -> SuiteResult:
        system_target = context.system_target
        if system_target.kind is TargetKind.FAKE:  # 앞 Suite 영향 없이 같은 결과
            system_target = build_fake_system_target(REGRESSION_RANDOM_SEED)
        system_target.verdict_reader.read_session_verdicts(context.run_id)  # 판정 미연결 = 트래픽 전 건너뜀

        planned_users = plan_virtual_users(TrafficMix(dict(REGRESSION_TRAFFIC_PERCENTS)), REGRESSION_VIRTUAL_USER_COUNT,
                                           REGRESSION_RANDOM_SEED)
        context.report_progress(f"회귀 트래픽 · VU {len(planned_users)} · 시드 {REGRESSION_RANDOM_SEED} · 봇 기본값")
        session_key_by_label, session_results = send_planned_user_traffic(
            context, planned_users, booking_api=system_target.booking_api,
            bot_profile_registry=build_regression_registry(), random_seed=REGRESSION_RANDOM_SEED, is_capture_saved=False,
        )
        answer_key = build_answer_key(planned_users, session_key_by_label)
        context.answer_key_repository.save_for_suite(context.run_id, self.suite_name, answer_key)  # QA DB만

        is_live = system_target.kind is TargetKind.LIVE
        if is_live:  # 트래픽 끝 → 파이프라인 판정 저장까지 · 바로 읽으면 판정 없음 = 전 유형 0%
            context.report_progress(f"판정 반영 대기 · {self.live_settle_sec:.0f}초")
            self.sleep(self.live_settle_sec)
        collection = collect_verdicts(
            lambda: system_target.verdict_reader.read_session_verdicts(context.run_id),
            select_judged_answer_key(answer_key, session_results), is_live, context.report_progress, self.sleep,
        )
        coverage_line = describe_verdict_collection(collection)
        blocked_line = describe_blocked_sessions(answer_key, session_results)
        if not collection.is_measurable:  # 판정 없음을 정상으로 세면 전 유형 0% · 기준선 비교 안 함
            return SuiteResult(self.suite_name, SuiteOutcome.SKIPPED,
                               message=MESSAGE_LINE_SEPARATOR.join([coverage_line, *([blocked_line] if blocked_line else [])]),
                               details={"verdict_received_count": collection.received_count,
                                        "judged_session_count": collection.judged_count})
        answer_key, system_verdicts = collection.judged_answer_key, collection.verdicts
        measured = calculate_detection_rate_by_actor(answer_key, system_verdicts)
        rule_set_version = read_rule_set_version(context, system_target.verdict_reader)
        baseline = load_baseline(context.case_data_dir, context.run_config.target_kind)

        lines: list[str] = []
        fail_lines: list[str] = []
        if baseline is None:
            lines.append(f"{REFERENCE_LINE_PREFIX}기준선 없음 · 이번 값이 첫 기준선 후보")
        elif not is_same_condition(baseline):
            lines.append(f"{REFERENCE_LINE_PREFIX}기준선 시드 · VU 수 · 트래픽 구성 다름 · 비교 안 함")
        else:
            fail_lines, reference_lines = compare_with_baseline(measured, baseline)
            if baseline.rule_set_version != rule_set_version:
                lines.append(f"규칙 버전 {baseline.rule_set_version or '-'} → {rule_set_version or '-'}")
            lines += reference_lines
        unlearned = calculate_unlearned_metrics(answer_key, system_verdicts)
        evaluation_ids = {actor_type.value for actor_type in list_evaluation_actor_types()}
        compared_count = sum(actor_id not in evaluation_ids for actor_id in measured)
        summary_line = f"비교 유형 {compared_count}종 · 하락 {len(fail_lines)}종"
        if unlearned:
            summary_line += f" · 미학습 탐지율 {next(iter(unlearned.values())):.1f}%(결과 공개)"
        return SuiteResult(
            suite_name=self.suite_name,
            outcome=SuiteOutcome.FAILED if fail_lines else SuiteOutcome.PASSED,
            metrics={
                "detection_regression_drop_count": float(len(fail_lines)),
                **{f"regression_{name}": value for name, value in unlearned.items()},
            },
            message=MESSAGE_LINE_SEPARATOR.join([*fail_lines, summary_line, coverage_line, *lines,
                                                 *([blocked_line] if blocked_line else [])]),
            details={
                "rule_set_version": rule_set_version,
                "random_seed": REGRESSION_RANDOM_SEED,
                "virtual_user_count": REGRESSION_VIRTUAL_USER_COUNT,
                "detection_rate_percent_by_actor": measured,
                "baseline_detection_rate_percent_by_actor": baseline.detection_rate_percent_by_actor if baseline else None,
            },
        )


def build_baseline_from_result(result: SuiteResult) -> RegressionBaseline:
    return RegressionBaseline(
        rule_set_version=result.details["rule_set_version"],
        random_seed=result.details["random_seed"],
        virtual_user_count=result.details["virtual_user_count"],
        traffic_percents=dict(REGRESSION_TRAFFIC_PERCENTS),
        detection_rate_percent_by_actor=dict(result.details["detection_rate_percent_by_actor"]),
    )
