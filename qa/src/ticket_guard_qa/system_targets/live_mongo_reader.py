"""시스템 MongoDB 읽기 · 읽기 전용 계정만 · 컬렉션은 docs/contracts.md 3장"""
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from pymongo import MongoClient
from ticket_guard_qa.domain.enums import VerdictStage
from ticket_guard_qa.domain.models import (
    LiveMetrics,
    QuarantineRecord,
    SessionFeatureRecord,
    SessionKey,
    SessionVerdictRecord,
    SystemVerdicts,
    verdicts_from_records,
)
from ticket_guard_qa.scoring.latency_metrics import calculate_p95
from ticket_guard_qa.system_targets.target_interfaces import StoredOutcome

SCHEMA_NOT_READY_MESSAGE = "MongoDB 스키마 확정 전 · docs/contracts.md 3장"
QA_READ_TIMEOUT_MS = 3000
PROCESSED_EVENT_COLLECTION_NAMES = ("clean_events", "quarantine_events")  # Spark 정제 · 격리 저장

# 판정 출처 · docs/contracts.md 3-1 · 세션 키 = bookingAttemptId (시도마다 새 값 · 이전 Run과 안 섞임)
VERDICT_LOOKBACK_SEC = 6 * 3600
SERVER_RESTRICTION_EVENT_TYPES = ("prevention_applied", "prevention_rejected")  # 서버 즉시 차단 → HOLD
SERVER_RESTRICTION_RULE_ID = "server_restriction"  # 정제 저장에 규칙 이름 없음
SERVER_EVALUATED_EVENT_TYPE = "prevention_evaluated"  # 서버 규칙 평가 · 적중 없으면 NORMAL
SERVER_RULE_MATCHES = (  # 서버가 거절한 단계 규칙 → CHALLENGE · 시스템 단계별 탐지와 같은 기준
    ("duplicate_progress", ("booking_access_rejected", "booking_confirm_rejected"), ("DUPLICATE_PAGE", "ACTIVE_ATTEMPT")),
    ("captcha_limit", ("captcha_limit_reached",), ("CAPTCHA_LIMIT",)),
    ("step_bypass", ("booking_access_rejected", "booking_confirm_rejected"), ("INVALID_STEP", "NOT_VERIFIED")),
    ("selection_condition", ("booking_confirm_fail",), ("SELECTION_LIMIT",)),
)
REJECTED_RESULTS = ("failure", "rejected")
STAGE_RANK = {VerdictStage.NORMAL: 0, VerdictStage.OBSERVE: 1, VerdictStage.CHALLENGE: 2, VerdictStage.HOLD: 3}


def as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)  # anomaly_events · epoch 초
    return None


def find_server_rule(document: dict) -> str | None:
    if document.get("result") not in REJECTED_RESULTS:
        return None
    for rule_id, event_types, error_codes in SERVER_RULE_MATCHES:
        if document.get("eventType") in event_types and document.get("errorCode") in error_codes:
            return rule_id
    return None


@dataclass
class SessionEvidence:
    """세션 하나의 판정 근거 · 가장 높은 단계"""
    stage: VerdictStage = VerdictStage.NORMAL
    rule_ids: set[str] = field(default_factory=set)
    rule_versions: set[str] = field(default_factory=set)
    decided_at: datetime | None = None

    def add(self, stage: VerdictStage, rule_id: str | None, occurred_at: datetime | None, rule_version: str = "") -> None:
        if STAGE_RANK[stage] > STAGE_RANK[self.stage]:
            self.stage = stage
        if rule_id:
            self.rule_ids.add(rule_id)
        if rule_version:
            self.rule_versions.add(rule_version)
        if occurred_at and (self.decided_at is None or occurred_at > self.decided_at):
            self.decided_at = occurred_at


class LiveMongoVerdictReader:
    """서버 차단 · 서버 단계 규칙(clean_events) + 파이프라인 이상 탐지(anomaly_events) → 세션별 판정 · 읽기만"""

    def __init__(self, mongo_uri: str, lookback_sec: float = VERDICT_LOOKBACK_SEC,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.mongo_uri = mongo_uri
        self.lookback_sec = lookback_sec
        self.clock = clock

    def read_session_verdicts(self, run_id: str) -> SystemVerdicts:
        return verdicts_from_records(self.read_verdict_records(run_id))

    def read_verdict_records(self, run_id: str) -> list[SessionVerdictRecord]:
        if not self.mongo_uri:
            raise NotImplementedError("시스템 MongoDB 읽기 미설정 · TICKET_GUARD_QA_SYSTEM_MONGO_URI · 읽기 전용 계정")
        since = self.clock() - timedelta(seconds=self.lookback_sec)
        evidence_by_session: dict[str, SessionEvidence] = {}
        with MongoClient(self.mongo_uri, serverSelectionTimeoutMS=QA_READ_TIMEOUT_MS, tz_aware=True) as client:
            database = client.get_default_database()
            server_event_types = [*SERVER_RESTRICTION_EVENT_TYPES, SERVER_EVALUATED_EVENT_TYPE,
                                  *(event_type for _, event_types, _ in SERVER_RULE_MATCHES for event_type in event_types)]
            clean_documents = database.clean_events.find(
                {"bookingAttemptId": {"$nin": [None, ""]}, "occurredAt": {"$gte": since},
                 "$or": [{"eventType": {"$in": server_event_types}}, {"evaluationStatus": "evaluated"}]},
                {"_id": 0, "bookingAttemptId": 1, "eventType": 1, "errorCode": 1, "result": 1, "occurredAt": 1},
            ).max_time_ms(QA_READ_TIMEOUT_MS)
            for document in clean_documents:
                evidence = evidence_by_session.setdefault(document["bookingAttemptId"], SessionEvidence())
                occurred_at = as_utc(document.get("occurredAt"))
                if document.get("eventType") in SERVER_RESTRICTION_EVENT_TYPES:
                    evidence.add(VerdictStage.HOLD, SERVER_RESTRICTION_RULE_ID, occurred_at)
                elif rule_id := find_server_rule(document):
                    evidence.add(VerdictStage.CHALLENGE, rule_id, occurred_at)
                else:
                    evidence.add(VerdictStage.NORMAL, None, occurred_at)  # 평가됨 · 적중 없음
            anomaly_documents = database.anomaly_events.find(
                {"isAnomaly": True, "attempt": {"$nin": [None, ""]}, "occurredAt": {"$gte": since.timestamp()}},
                {"_id": 0, "attempt": 1, "rule": 1, "ruleVersion": 1, "occurredAt": 1},
            ).max_time_ms(QA_READ_TIMEOUT_MS)
            for document in anomaly_documents:  # 철회된 신호 = isAnomaly False · 제외
                evidence_by_session.setdefault(document["attempt"], SessionEvidence()).add(
                    VerdictStage.CHALLENGE, document.get("rule"), as_utc(document.get("occurredAt")), document.get("ruleVersion") or "")
        return [
            SessionVerdictRecord(
                booking_attempt_id=SessionKey(session_key),
                stage=evidence.stage,
                score=0.0,  # 시스템 점수 없음
                matched_rules=tuple(sorted(evidence.rule_ids)),
                rule_set_version="|".join(sorted(evidence.rule_versions)),
                decided_at=evidence.decided_at or since,
            )
            for session_key, evidence in evidence_by_session.items()
        ]


GOLD_LATENCY_WINDOW_SEC = 15 * 60  # Run 하나의 트래픽 범위 · Gold 반영 지연 Suite는 트래픽 Suite 뒤에 실행
LIVE_METRICS_WINDOW_SEC = 30.0  # 03 화면 · 최근 30초
LIVE_METRICS_CACHE_SEC = 5.0  # 1초마다 호출 · DB 조회는 5초에 한 번
MAX_LATENCY_SAMPLE_COUNT = 5000


def read_reflection_latencies_sec(database, since: datetime) -> list[float]:
    """서버 이벤트만 · 브라우저 시계 오차 제외 · 발생(occurredAt) → 정제 저장(normalizedAt)"""
    documents = database.clean_events.find(
        {"source": "server", "occurredAt": {"$gte": since}, "normalizedAt": {"$ne": None}},
        {"_id": 0, "occurredAt": 1, "normalizedAt": 1},
    ).limit(MAX_LATENCY_SAMPLE_COUNT).max_time_ms(QA_READ_TIMEOUT_MS)
    latencies = []
    for document in documents:
        occurred_at, normalized_at = as_utc(document.get("occurredAt")), as_utc(document.get("normalizedAt"))
        if occurred_at and normalized_at and normalized_at >= occurred_at:
            latencies.append((normalized_at - occurred_at).total_seconds())
    return latencies


class LiveMongoLiveMetricsReader:
    """03 화면 실시간 수치 · 처리량 · Gold 반영 지연 · 판정 API 지연 · 오류율은 시스템 기록 없음 → None(측정 전)"""

    def __init__(self, mongo_uri: str, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.mongo_uri = mongo_uri
        self.clock = clock
        self.monotonic = monotonic
        self.cached_metrics: LiveMetrics | None = None
        self.cached_at = float("-inf")

    def read_live_metrics(self, run_id: str, elapsed_sec: float) -> LiveMetrics:
        if not self.mongo_uri:
            raise NotImplementedError("시스템 MongoDB 읽기 미설정 · TICKET_GUARD_QA_SYSTEM_MONGO_URI")
        if self.cached_metrics and self.monotonic() - self.cached_at < LIVE_METRICS_CACHE_SEC:
            return self.cached_metrics
        since = self.clock() - timedelta(seconds=LIVE_METRICS_WINDOW_SEC)
        with MongoClient(self.mongo_uri, serverSelectionTimeoutMS=QA_READ_TIMEOUT_MS, tz_aware=True) as client:
            database = client.get_default_database()
            processed_count = database.clean_events.count_documents({"normalizedAt": {"$gte": since}}, maxTimeMS=QA_READ_TIMEOUT_MS)
            latencies = read_reflection_latencies_sec(database, since)
        self.cached_metrics = LiveMetrics(
            api_latency_p95_sec=None,
            api_error_rate_percent=None,
            events_per_sec=round(processed_count / LIVE_METRICS_WINDOW_SEC, 1),
            gold_latency_p95_sec=round(calculate_p95(latencies), 2) if latencies else None,
        )
        self.cached_at = self.monotonic()
        return self.cached_metrics


class LiveMongoPipelineReader:
    def __init__(self, mongo_uri: str, topic: str = "",
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.mongo_uri = mongo_uri
        self.topic = topic
        self.clock = clock

    def read_stored_outcomes(self, positions: list[tuple[int, int]]) -> list[StoredOutcome]:
        """Spark 저장 문서 _id = 토픽:파티션:오프셋 · 정제 · 격리 컬렉션 조회 · 읽기만"""
        if not self.mongo_uri:
            raise NotImplementedError("시스템 MongoDB 읽기 미설정 · TICKET_GUARD_QA_SYSTEM_MONGO_URI · 읽기 전용 계정")
        document_ids = [f"{self.topic}:{partition}:{offset}" for partition, offset in positions]
        outcomes = []
        with MongoClient(self.mongo_uri, serverSelectionTimeoutMS=QA_READ_TIMEOUT_MS) as client:
            database = client.get_default_database()
            for collection_name in PROCESSED_EVENT_COLLECTION_NAMES:
                for document in database[collection_name].find({"_id": {"$in": document_ids}}):
                    outcomes.append(StoredOutcome(
                        int(document["partition"]), int(document["offset"]), collection_name,
                        document.get("reasonCode") or document.get("reason"),
                    ))
        return outcomes

    def find_processed_event_ids(self, event_ids: list[str]) -> list[str]:
        """정제 · 격리 컬렉션 eventId 조회 · 읽기만 · 중복 저장은 중복 그대로"""
        if not self.mongo_uri:
            raise NotImplementedError("시스템 MongoDB 읽기 미설정 · TICKET_GUARD_QA_SYSTEM_MONGO_URI · 읽기 전용 계정")
        with MongoClient(self.mongo_uri, serverSelectionTimeoutMS=QA_READ_TIMEOUT_MS) as client:
            database = client.get_default_database()
            return [
                document["eventId"]
                for collection_name in PROCESSED_EVENT_COLLECTION_NAMES
                for document in database[collection_name].find({"eventId": {"$in": event_ids}}, {"_id": 0, "eventId": 1})
            ]

    def summary(self) -> dict[str, Any]:
        """Read dashboard metrics without mutating the system database."""
        if not self.mongo_uri:
            raise ValueError("system_mongo_uri is not configured")
        with MongoClient(self.mongo_uri, serverSelectionTimeoutMS=3000, readPreference='secondaryPreferred') as client:
            database = client.get_default_database()
            clean = database.clean_events.count_documents({})
            quarantine = database.quarantine_events.count_documents({})
            metrics = list(database.event_metrics.find({}, {'_id': 0}).sort('eventType', 1))
        return {
            'clean_events': clean,
            'quarantine_events': quarantine,
            'metrics': metrics,
        }

    def read_gold_latency_sec(self, run_id: str) -> list[float]:
        """최근 Run 범위 서버 이벤트 · 발생 → 정제 저장(같은 배치에서 집계 갱신)까지 초"""
        if not self.mongo_uri:
            raise NotImplementedError("시스템 MongoDB 읽기 미설정 · TICKET_GUARD_QA_SYSTEM_MONGO_URI · 읽기 전용 계정")
        since = self.clock() - timedelta(seconds=GOLD_LATENCY_WINDOW_SEC)
        with MongoClient(self.mongo_uri, serverSelectionTimeoutMS=QA_READ_TIMEOUT_MS, tz_aware=True) as client:
            return read_reflection_latencies_sec(client.get_default_database(), since)

    def count_quarantined_events(self, run_id: str) -> int:
        """QA 시험용 · 05 화면 summary()와 분리 · summary 반환 모양이 바뀌어도 영향 없음"""
        if not self.mongo_uri:
            raise ValueError("system_mongo_uri is not configured")
        with MongoClient(self.mongo_uri, serverSelectionTimeoutMS=QA_READ_TIMEOUT_MS) as client:
            # TODO(Run 구분 필드 확정 후): run_id 범위로 좁히기 · 지금은 전체 건수
            return int(client.get_default_database().quarantine_events.count_documents({}))

    def read_session_features(self, run_id: str) -> list[SessionFeatureRecord]:
        # TODO(스키마 확정 후): SESSION_FEATURES
        raise NotImplementedError(SCHEMA_NOT_READY_MESSAGE)

    def read_quarantine_records(self, run_id: str) -> list[QuarantineRecord]:
        # TODO(스키마 확정 후): QUARANTINE_EVENTS
        raise NotImplementedError(SCHEMA_NOT_READY_MESSAGE)
