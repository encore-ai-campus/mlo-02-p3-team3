"""확장 G · H · I · 미학습 유형 확대 · 탐지 회귀(기준선 비교) · 장애 중 탐지(처리 중지 중 트래픽 판정)"""
from dataclasses import replace

import pytest

from ticket_guard_qa.answer_key.answer_key_repository import AnswerKeyRepository
from ticket_guard_qa.domain.enums import ActorType, SuiteOutcome, TargetKind, VerdictStage
from ticket_guard_qa.domain.models import FAIL_LINE_PREFIX, MESSAGE_LINE_SEPARATOR, RunConfig, SessionKey, TrafficMix
from ticket_guard_qa.domain.rule_sets import find_matched_rule_ids
from ticket_guard_qa.macro_bots.bot_catalog import BotRole, find_bot_type_info, list_bot_types
from ticket_guard_qa.macro_bots.bot_profiles import DEFAULT_BEHAVIOR_BY_ACTOR_TYPE, BotProfile, BotProfileRegistry
from ticket_guard_qa.scoring.detection_metrics import (
    UNLEARNED_DETECTION_METRIC_NAME,
    calculate_detection_rate_by_actor,
    calculate_unlearned_metrics,
)
from ticket_guard_qa.settings import QA_ROOT_DIR
from ticket_guard_qa.system_targets.fake_system import build_fake_system_target
from ticket_guard_qa.system_targets.live_django_system import LiveDjangoVerdictReader, LiveDockerProcessControl
from ticket_guard_qa.system_targets.target_interfaces import PIPELINE_SERVICE_NAME
from ticket_guard_qa.test_suites.detection_regression import (
    ALLOWED_CHANGE_PERCENT_POINT,
    REGRESSION_RANDOM_SEED,
    REGRESSION_TRAFFIC_PERCENTS,
    REGRESSION_VIRTUAL_USER_COUNT,
    DetectionRegressionSuite,
    RegressionBaseline,
    build_baseline_from_result,
    compare_with_baseline,
    load_baseline,
    save_baseline,
)
from ticket_guard_qa.test_suites.failure_detection import FailureDetectionSuite, wait_until_judged
from ticket_guard_qa.test_suites.suite_interface import SuiteContext
from ticket_guard_qa.test_suites.suite_registry import SUITE_BY_NAME

CASE_DATA_DIR = QA_ROOT_DIR / "case_data"
UNLEARNED_TYPES = (ActorType.BOT_NEW_SLOW_REGULAR, ActorType.BOT_NEW_SPLIT_ACCOUNT, ActorType.BOT_NEW_HUMAN_MIMIC)
SMALL_MIX = TrafficMix({"HUMAN": 80, "BOT_A": 10, "BOT_D": 10})  # 봇 C 제외 · 표본 작은 Mock 흔들림 없이


def build_context(qa_database, system_target=None, run_config=None, **fields) -> SuiteContext:
    return SuiteContext(
        run_id="run-expansion", run_config=run_config or RunConfig(TargetKind.FAKE, ("detection_regression",)),
        system_target=system_target or build_fake_system_target(), answer_key_repository=AnswerKeyRepository(qa_database),
        case_data_dir=CASE_DATA_DIR, report_progress=lambda text, **progress: None, **fields,
    )


def failure_lines(message: str) -> list[str]:
    return [line for line in message.split(MESSAGE_LINE_SEPARATOR) if line.startswith(FAIL_LINE_PREFIX)]


# G · 미학습 유형 확대

@pytest.mark.parametrize("actor_type", UNLEARNED_TYPES)
def test_unlearned_types_are_locked_evaluation_only_and_outside_traffic_mix(actor_type):
    info = find_bot_type_info(actor_type)
    assert info.role is BotRole.EVALUATION_ONLY and info.detection_target_id is None and info.is_locked
    assert not info.is_in_traffic_mix  # 02 트래픽 구성 밖 · 탐지 회귀에서만 실행
    assert actor_type in DEFAULT_BEHAVIOR_BY_ACTOR_TYPE


def test_unlearned_short_names_are_unique():
    short_names = [info.short_name for info in list_bot_types()]
    assert len(short_names) == len(set(short_names))


def test_unlearned_combinations_sit_on_rule_boundaries():
    behavior = DEFAULT_BEHAVIOR_BY_ACTOR_TYPE
    assert find_matched_rule_ids(behavior[ActorType.BOT_NEW_SLOW_REGULAR]) == ("MOCK-R1",)  # 간격 편차만
    assert "MOCK-R3" not in find_matched_rule_ids(behavior[ActorType.BOT_NEW_SPLIT_ACCOUNT])  # 기기당 2 = 기준(> 2) 아래
    assert find_matched_rule_ids(behavior[ActorType.BOT_NEW_HUMAN_MIMIC]) == ()  # 규칙 밖 · 결과 공개로 빈틈 확인


def test_rate_by_actor_and_unlearned_metric():
    answer_key = {SessionKey("s1"): ActorType.HUMAN, SessionKey("s2"): ActorType.BOT_A,
                  SessionKey("s3"): ActorType.BOT_NEW, SessionKey("s4"): ActorType.BOT_NEW_HUMAN_MIMIC}
    verdicts = {SessionKey("s2"): VerdictStage.HOLD, SessionKey("s3"): VerdictStage.CHALLENGE}
    assert calculate_detection_rate_by_actor(answer_key, verdicts) == {
        "BOT_A": 100.0, "BOT_NEW": 100.0, "BOT_NEW_HUMAN_MIMIC": 0.0, "HUMAN": 0.0}
    assert calculate_unlearned_metrics(answer_key, verdicts) == {UNLEARNED_DETECTION_METRIC_NAME: 50.0}
    assert calculate_unlearned_metrics({SessionKey("s1"): ActorType.HUMAN}, {}) == {}


def test_suite_answer_keys_are_separate_and_deleted_with_run(qa_database):
    repository = AnswerKeyRepository(qa_database)
    repository.save("run-a", {SessionKey("s1"): ActorType.HUMAN})
    repository.save_for_suite("run-a", "detection_regression", {SessionKey("s9"): ActorType.BOT_A})
    repository.save("run-ab", {SessionKey("s2"): ActorType.HUMAN})
    assert repository.load("run-a") == {SessionKey("s1"): ActorType.HUMAN}  # B4 · 05는 Run 정답표만
    assert repository.load_for_suite("run-a", "detection_regression") == {SessionKey("s9"): ActorType.BOT_A}
    repository.delete("run-a")
    assert repository.load("run-a") == {} and repository.load_for_suite("run-a", "detection_regression") == {}
    assert repository.load("run-ab") == {SessionKey("s2"): ActorType.HUMAN}  # 이름이 비슷한 다른 Run은 그대로


# H · 탐지 회귀

def test_regression_traffic_covers_every_built_in_type():
    TrafficMix(dict(REGRESSION_TRAFFIC_PERCENTS))  # 합계 100
    built_in = {info.actor_type.value for info in list_bot_types() if not info.is_custom}
    assert set(REGRESSION_TRAFFIC_PERCENTS) == built_in


def test_mock_regression_matches_checked_in_baseline_every_time(qa_database):
    first = DetectionRegressionSuite().run(build_context(qa_database))
    second = DetectionRegressionSuite().run(build_context(qa_database))
    assert first.outcome is SuiteOutcome.PASSED, first.message
    assert first.details["detection_rate_percent_by_actor"] == second.details["detection_rate_percent_by_actor"]
    baseline = load_baseline(CASE_DATA_DIR, TargetKind.FAKE)
    assert first.details["detection_rate_percent_by_actor"] == baseline.detection_rate_percent_by_actor
    saved = AnswerKeyRepository(qa_database).load_for_suite("run-expansion", "detection_regression")
    assert len(saved) == REGRESSION_VIRTUAL_USER_COUNT


def test_regression_ignores_saved_bot_versions_and_earlier_suites(qa_database):
    slow_bot_a = replace(DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_A], click_interval_mean_ms=5_000,
                         click_interval_deviation_ms=4_000, entry_delay_after_open_ms=9_000)
    registry = BotProfileRegistry((BotProfile(ActorType.BOT_A, slow_bot_a, version_number=7),))
    system_target = build_fake_system_target()
    system_target.booking_api.record_session(DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.HUMAN])  # 앞 Suite 세션
    result = DetectionRegressionSuite().run(build_context(qa_database, system_target, bot_profile_registry=registry))
    assert result.outcome is SuiteOutcome.PASSED, result.message  # 봇 기본값 · 새 가짜 대상


def test_regression_skips_before_traffic_without_verdict_reader(qa_database):
    system_target = replace(build_fake_system_target(), kind=TargetKind.LIVE, verdict_reader=LiveDjangoVerdictReader())
    run_config = RunConfig(TargetKind.LIVE, ("detection_regression",))
    with pytest.raises(NotImplementedError):
        DetectionRegressionSuite().run(build_context(qa_database, system_target, run_config))
    assert system_target.booking_api.state.session_count == 0


def make_baseline(rates: dict[str, float]) -> RegressionBaseline:
    return RegressionBaseline("mock-1", REGRESSION_RANDOM_SEED, REGRESSION_VIRTUAL_USER_COUNT,
                              dict(REGRESSION_TRAFFIC_PERCENTS), rates)


def test_compare_flags_bot_drop_and_human_rise_beyond_tolerance():
    baseline = make_baseline({"BOT_A": 100.0, "BOT_B": 80.0, "HUMAN": 1.0, "BOT_C": 70.0})
    tolerance = ALLOWED_CHANGE_PERCENT_POINT
    fails, references = compare_with_baseline(
        {"BOT_A": 100.0 - tolerance, "BOT_B": 60.0, "HUMAN": 1.0 + tolerance + 0.1, "BOT_REENTRY": 100.0}, baseline)
    assert fails == [f"{FAIL_LINE_PREFIX}봇 B 탐지율 하락 · 80.0% → 60.0%",
                     f"{FAIL_LINE_PREFIX}정상 오탐률 상승 · 1.0% → 6.1%"]
    assert any("봇 F 기준선 없음" in line for line in references)
    assert any("봇 C 이번 Run 세션 없음" in line for line in references)


def test_baseline_round_trip_and_rule_change_is_reported(qa_database, tmp_path):
    result = DetectionRegressionSuite().run(build_context(qa_database))
    path = save_baseline(tmp_path, TargetKind.FAKE, replace(build_baseline_from_result(result), rule_set_version="mock-0"))
    assert path == tmp_path / "regression_baselines" / "fake.json"
    changed = DetectionRegressionSuite().run(replace(build_context(qa_database), case_data_dir=tmp_path))
    assert changed.outcome is SuiteOutcome.PASSED and "규칙 버전 mock-0 → mock-1" in changed.message


def test_baseline_with_other_conditions_is_not_compared(qa_database, tmp_path):
    save_baseline(tmp_path, TargetKind.FAKE, replace(make_baseline({"BOT_A": 100.0}), random_seed=1))
    result = DetectionRegressionSuite().run(replace(build_context(qa_database), case_data_dir=tmp_path))
    assert result.outcome is SuiteOutcome.PASSED and "비교 안 함" in result.message


# I · 장애 중 탐지

def failure_context(qa_database, system_target):
    run_config = RunConfig(TargetKind.FAKE, ("failure_detection",), virtual_user_count=40, traffic_mix=SMALL_MIX)
    return build_context(qa_database, system_target, run_config)


def run_failure_detection(qa_database, system_target):
    return FailureDetectionSuite(timeout_sec=0.0, poll_interval_sec=0.0).run(failure_context(qa_database, system_target))


def test_sessions_during_outage_are_judged_after_restart(qa_database):
    system_target = build_fake_system_target()
    result = run_failure_detection(qa_database, system_target)
    assert result.outcome is SuiteOutcome.PASSED, result.message
    assert result.metrics["failure_detection_missing_verdict_count"] == 0.0
    assert "failure_detection_verdict_delay_sec" in result.metrics
    assert "failure_detection_bot_abd_detection_rate_percent" in result.metrics
    assert "bot_abd_detection_rate_percent" not in result.metrics  # 판정 정확도 합격 기준과 섞이지 않게
    assert system_target.booking_api.state.is_pipeline_running and not system_target.booking_api.state.pending_judgements


def test_mock_holds_verdicts_while_pipeline_stopped():
    system_target = build_fake_system_target()
    state = system_target.booking_api.state
    system_target.process_control.stop_service(PIPELINE_SERVICE_NAME)
    first = system_target.booking_api.record_session(DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_A])
    second = system_target.booking_api.record_session(DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.HUMAN])
    assert first.session_key != second.session_key and not state.session_verdicts  # 번호 겹침 없음 · 판정 대기
    system_target.process_control.start_service(PIPELINE_SERVICE_NAME)
    assert set(state.session_verdicts) == {first.session_key, second.session_key}


def test_lost_backlog_leaves_outage_sessions_without_verdict(qa_database):
    system_target = build_fake_system_target()
    system_target.booking_api.state.is_backlog_lost = True
    result = run_failure_detection(qa_database, system_target)
    assert result.outcome is SuiteOutcome.FAILED
    assert failure_lines(result.message)[0].startswith(f"{FAIL_LINE_PREFIX}재시작 후 판정 누락 40세션")
    assert "failure_detection_verdict_delay_sec" not in result.metrics


def test_failure_detection_skips_without_process_control(qa_database):
    system_target = replace(build_fake_system_target(),
                            process_control=LiveDockerProcessControl({PIPELINE_SERVICE_NAME: ""}, "/var/run/docker.sock"))
    with pytest.raises(NotImplementedError, match="TICKET_GUARD_QA_PIPELINE_CONTAINER_NAME"):
        run_failure_detection(qa_database, system_target)
    assert system_target.booking_api.state.session_count == 0  # 트래픽 전에 건너뜀


def test_wait_until_judged_polls_until_every_session_has_verdict():
    answer_key = {SessionKey("s1"): ActorType.HUMAN, SessionKey("s2"): ActorType.BOT_A}

    class SlowVerdictReader:
        calls = 0

        def read_session_verdicts(self, run_id):
            self.calls += 1
            return {SessionKey("s1"): VerdictStage.NORMAL, **({SessionKey("s2"): VerdictStage.HOLD} if self.calls >= 3 else {})}

    reader = SlowVerdictReader()
    verdicts, _ = wait_until_judged(reader, "run", answer_key, timeout_sec=60.0, poll_interval_sec=0.0, sleep=lambda _: None)
    assert reader.calls == 3 and verdicts.keys() == answer_key.keys()


def test_new_suites_are_registered():
    assert {"detection_regression", "failure_detection"} <= SUITE_BY_NAME.keys()


def test_regression_follows_run_virtual_user_count(qa_database):
    run_config = RunConfig(TargetKind.FAKE, ("detection_regression",), virtual_user_count=10)
    result = DetectionRegressionSuite().run(build_context(qa_database, run_config=run_config))
    assert len(AnswerKeyRepository(qa_database).load_for_suite("run-expansion", "detection_regression")) == 10
    assert result.details["virtual_user_count"] == 10
    assert "기준선 VU 200 · 이번 VU 10 · 조건 다름 · 비교 안 함" in result.message  # 다른 인원 기준선과 비교 안 함
