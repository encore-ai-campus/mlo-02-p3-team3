"""Live 판정 읽기 · 서버 차단 · 서버 단계 규칙 · 파이프라인 이상 탐지 → 세션별 판정 · E22"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import mongomock
import pytest

from ticket_guard_qa.answer_key.answer_key_repository import AnswerKeyRepository
from ticket_guard_qa.domain.enums import TargetKind, VerdictStage
from ticket_guard_qa.domain.models import RunConfig
from ticket_guard_qa.settings import QA_ROOT_DIR
from ticket_guard_qa.system_targets import live_mongo_reader
from ticket_guard_qa.system_targets.fake_system import build_fake_system_target
from ticket_guard_qa.system_targets.live_mongo_reader import LiveMongoVerdictReader
from ticket_guard_qa.test_suites.suite_interface import SuiteContext
from ticket_guard_qa.test_suites.verdict_accuracy import VerdictAccuracySuite

SYSTEM_MONGO_URI = "mongodb://reader@system-host:27017/ticketguard"
NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)


def build_reader_with(clean_events: list[dict], anomaly_events: list[dict] = ()) -> tuple[LiveMongoVerdictReader, mongomock.MongoClient]:
    memory_client = mongomock.MongoClient(SYSTEM_MONGO_URI, tz_aware=True)
    database = memory_client["ticketguard"]
    if clean_events:
        database.clean_events.insert_many(clean_events)
    if anomaly_events:
        database.anomaly_events.insert_many(list(anomaly_events))
    return LiveMongoVerdictReader(SYSTEM_MONGO_URI, clock=lambda: NOW), memory_client


def clean_event(attempt_id: str, event_type: str, minutes_ago: float = 1, **fields) -> dict:
    return {"bookingAttemptId": attempt_id, "eventType": event_type, "occurredAt": NOW - timedelta(minutes=minutes_ago), **fields}


def read_records(reader: LiveMongoVerdictReader, memory_client: mongomock.MongoClient):
    with patch.object(live_mongo_reader, "MongoClient", lambda *args, **kwargs: memory_client):
        return {record.booking_attempt_id: record for record in reader.read_verdict_records("run-test")}


def test_server_restriction_is_hold():
    reader, client = build_reader_with([clean_event("attempt-1", "prevention_applied", result="success")])
    record = read_records(reader, client)["attempt-1"]
    assert record.stage is VerdictStage.HOLD
    assert record.matched_rules == ("server_restriction",)


def test_server_rule_rejection_is_challenge():
    reader, client = build_reader_with([
        clean_event("attempt-1", "booking_access_rejected", result="rejected", errorCode="DUPLICATE_PAGE"),
        clean_event("attempt-2", "booking_confirm_rejected", result="rejected", errorCode="NOT_VERIFIED"),
    ])
    records = read_records(reader, client)
    assert records["attempt-1"].stage is VerdictStage.CHALLENGE
    assert records["attempt-1"].matched_rules == ("duplicate_progress",)
    assert records["attempt-2"].matched_rules == ("step_bypass",)


def test_unrelated_rejection_is_not_detection():
    reader, client = build_reader_with([
        clean_event("attempt-1", "booking_confirm_fail", result="failure", errorCode="SOLD_OUT", evaluationStatus="evaluated"),
    ])
    assert read_records(reader, client)["attempt-1"].stage is VerdictStage.NORMAL  # 매진은 규칙 아님


def test_pipeline_anomaly_is_challenge_and_retracted_is_ignored():
    reader, client = build_reader_with(
        [clean_event("attempt-1", "queue_enter", evaluationStatus="evaluated")],
        [
            {"attempt": "attempt-1", "isAnomaly": True, "rule": "retry", "ruleVersion": "v1", "occurredAt": NOW.timestamp() - 60},
            {"attempt": "attempt-2", "isAnomaly": False, "rule": "overlap", "ruleVersion": "v1", "occurredAt": NOW.timestamp() - 60},
        ],
    )
    records = read_records(reader, client)
    assert records["attempt-1"].stage is VerdictStage.CHALLENGE
    assert records["attempt-1"].matched_rules == ("retry",)
    assert records["attempt-1"].rule_set_version == "v1"
    assert "attempt-2" not in records  # 철회된 신호


def test_highest_stage_wins_and_rules_are_combined():
    reader, client = build_reader_with(
        [clean_event("attempt-1", "prevention_rejected", result="rejected"),
         clean_event("attempt-1", "booking_access_rejected", result="rejected", errorCode="ACTIVE_ATTEMPT")],
        [{"attempt": "attempt-1", "isAnomaly": True, "rule": "pages", "ruleVersion": "v1", "occurredAt": NOW.timestamp() - 30}],
    )
    record = read_records(reader, client)["attempt-1"]
    assert record.stage is VerdictStage.HOLD
    assert record.matched_rules == ("duplicate_progress", "pages", "server_restriction")


def test_evaluated_session_without_hit_is_normal():
    reader, client = build_reader_with([clean_event("attempt-1", "prevention_evaluated", result="success")])
    assert read_records(reader, client)["attempt-1"].stage is VerdictStage.NORMAL


def test_old_and_unkeyed_events_are_excluded():
    reader, client = build_reader_with([
        clean_event("attempt-old", "prevention_applied", minutes_ago=7 * 60),
        {"bookingAttemptId": None, "eventType": "prevention_applied", "occurredAt": NOW},
    ])
    assert read_records(reader, client) == {}


def test_session_verdicts_match_records():
    reader, client = build_reader_with([clean_event("attempt-1", "prevention_applied")])
    with patch.object(live_mongo_reader, "MongoClient", lambda *args, **kwargs: client):
        assert reader.read_session_verdicts("run-test") == {"attempt-1": VerdictStage.HOLD}


def run_verdict_accuracy(qa_database, target_kind: TargetKind) -> list[float]:
    slept_seconds: list[float] = []
    context = SuiteContext(
        run_id="run-test",
        run_config=RunConfig(TargetKind.FAKE, ("verdict_accuracy",), virtual_user_count=20),
        system_target=replace(build_fake_system_target(), kind=target_kind),
        answer_key_repository=AnswerKeyRepository(qa_database),
        case_data_dir=QA_ROOT_DIR / "case_data",
        report_progress=lambda text, **fields: None,
    )
    VerdictAccuracySuite(live_settle_sec=30.0, sleep=slept_seconds.append).run(context)
    return slept_seconds


def test_live_run_waits_for_pipeline_verdicts(qa_database):
    assert run_verdict_accuracy(qa_database, TargetKind.LIVE) == [30.0]


def test_fake_run_does_not_wait(qa_database):
    assert run_verdict_accuracy(qa_database, TargetKind.FAKE) == []


def test_verdict_reader_needs_mongo_uri():
    with pytest.raises(NotImplementedError):  # 미설정 = 판정 정확도 Suite 건너뜀
        LiveMongoVerdictReader("").read_session_verdicts("run-test")


def build_pipeline_database(events: list[dict]) -> mongomock.MongoClient:
    memory_client = mongomock.MongoClient(SYSTEM_MONGO_URI, tz_aware=True)
    if events:
        memory_client["ticketguard"].clean_events.insert_many(events)
    return memory_client


def server_event(seconds_ago: float, latency_sec: float, source: str = "server") -> dict:
    occurred_at = NOW - timedelta(seconds=seconds_ago)
    return {"source": source, "occurredAt": occurred_at, "normalizedAt": occurred_at + timedelta(seconds=latency_sec)}


def test_gold_latency_uses_recent_server_events_only():
    client = build_pipeline_database([
        server_event(60, 2.5), server_event(120, 4.0),
        server_event(60, 30.0, source="browser"),  # 브라우저 시계 · 제외
        server_event(20 * 60, 9.0),  # 15분 밖 · 제외
    ])
    reader = live_mongo_reader.LiveMongoPipelineReader(SYSTEM_MONGO_URI, clock=lambda: NOW)
    with patch.object(live_mongo_reader, "MongoClient", lambda *args, **kwargs: client):
        assert sorted(reader.read_gold_latency_sec("run-test")) == [2.5, 4.0]


def test_live_metrics_report_throughput_and_gold_latency():
    client = build_pipeline_database([server_event(5, 1.0), server_event(10, 3.0), server_event(200, 1.0)])
    reader = live_mongo_reader.LiveMongoLiveMetricsReader(SYSTEM_MONGO_URI, clock=lambda: NOW, monotonic=lambda: 0.0)
    with patch.object(live_mongo_reader, "MongoClient", lambda *args, **kwargs: client):
        metrics = reader.read_live_metrics("run-test", 10.0)
    assert metrics.events_per_sec == round(2 / 30, 1)
    assert metrics.gold_latency_p95_sec == 3.0
    assert metrics.api_latency_p95_sec is None  # 시스템 기록 없음 · 측정 전


def test_live_metrics_are_cached_between_ticks():
    reader = live_mongo_reader.LiveMongoLiveMetricsReader(SYSTEM_MONGO_URI, clock=lambda: NOW, monotonic=lambda: 0.0)
    calls = []
    client = build_pipeline_database([server_event(5, 1.0)])
    with patch.object(live_mongo_reader, "MongoClient", lambda *args, **kwargs: calls.append(1) or client):
        reader.read_live_metrics("run-test", 1.0)
        reader.read_live_metrics("run-test", 2.0)
    assert len(calls) == 1


def test_pipeline_latency_skips_without_recent_events(qa_database):
    from ticket_guard_qa.domain.enums import SuiteOutcome
    from ticket_guard_qa.test_suites.pipeline_latency import PipelineLatencySuite

    class EmptyPipelineReader:
        def read_gold_latency_sec(self, run_id):
            return []

    context = SuiteContext(
        run_id="run-test",
        run_config=RunConfig(TargetKind.FAKE, ("pipeline_latency",)),
        system_target=replace(build_fake_system_target(), pipeline_reader=EmptyPipelineReader()),
        answer_key_repository=AnswerKeyRepository(qa_database),
        case_data_dir=QA_ROOT_DIR / "case_data",
        report_progress=lambda text, **fields: None,
    )
    assert PipelineLatencySuite().run(context).outcome is SuiteOutcome.SKIPPED
