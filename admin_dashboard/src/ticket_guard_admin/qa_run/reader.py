"""QA Run 구간 · 시험 계정 트래픽만 · run_id 시작 시각 + 전달된 종료 시각 · 시스템 DB에 QA 필드 없음"""
import re
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from ticket_guard_admin.mongo_reader import FIELDS, KST, MAX_EVENTS, READ_TIMEOUT_MS, build_dashboard

RUN_ID_PATTERN = re.compile(r"run-(\d{8}-\d{6})-[0-9a-f]{4}")  # QA create_run_id · 시작 시각 UTC
DEFAULT_RUN_WINDOW = timedelta(minutes=60)  # 종료 시각 미전달 · 시작 후 최대 구간
MAX_RUN_WINDOW = timedelta(hours=6)
START_MARGIN = timedelta(seconds=5)  # Run 기록 직후 첫 요청 · 시계 차이


class QaRunReadError(ValueError):
    pass


def parse_run_started_at(run_id: str) -> datetime | None:
    match = RUN_ID_PATTERN.fullmatch(run_id or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_run_finished_at(value: str | None, started_at: datetime) -> datetime | None:
    """전달된 종료 시각 · 형식 오류 · 시작 전 · 6시간 초과 = 무시"""
    if not value:
        return None
    try:
        finished_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    finished_at = finished_at.replace(tzinfo=timezone.utc) if finished_at.tzinfo is None else finished_at
    return finished_at if started_at <= finished_at <= started_at + MAX_RUN_WINDOW else None


def build_run_window(run_id: str, run_to: str | None, now: datetime) -> tuple[datetime, datetime, bool]:
    started_at = parse_run_started_at(run_id)
    if started_at is None:
        raise QaRunReadError("Run 번호 형식 불일치")
    finished_at = parse_run_finished_at(run_to, started_at)
    if finished_at is not None:
        return started_at - START_MARGIN, finished_at, True
    return started_at - START_MARGIN, min(started_at + DEFAULT_RUN_WINDOW, now), False


def format_kst(value: datetime) -> str:
    return value.astimezone(KST).strftime("%m-%d %H:%M:%S")


def read_qa_run(mongo_uri: str, run_id: str, run_to: str | None = None, now: datetime | None = None) -> dict:
    """Run 구간 · isSimulation(시험 계정) 이벤트 집계 · 화면에는 집계값만"""
    if not mongo_uri:
        raise QaRunReadError("시스템 MongoDB 조회 설정 없음")
    now = now or datetime.now(timezone.utc)
    window_start, window_end, has_finish = build_run_window(run_id, run_to, now)
    query = {"occurredAt": {"$gte": window_start, "$lte": window_end}, "isSimulation": True}
    try:
        with MongoClient(mongo_uri, serverSelectionTimeoutMS=READ_TIMEOUT_MS, connectTimeoutMS=READ_TIMEOUT_MS,
                         socketTimeoutMS=READ_TIMEOUT_MS, tz_aware=True, appname="ticketguard-admin-qa-run") as client:
            database = client.get_default_database()
            matched = database.clean_events.count_documents(query, maxTimeMS=READ_TIMEOUT_MS)
            events = list(database.clean_events.find(query, FIELDS).sort("occurredAt", -1)
                          .limit(MAX_EVENTS).max_time_ms(READ_TIMEOUT_MS))
    except PyMongoError:
        raise QaRunReadError("시스템 MongoDB 조회 실패 · 연결 · 읽기 권한 확인 필요") from None
    dashboard = build_dashboard(events)
    return {
        "run_id": run_id,
        "window_label": f"{format_kst(window_start)} ~ {format_kst(window_end)} KST",
        "has_finish": has_finish,
        "matched": matched,
        "is_truncated": matched > MAX_EVENTS,
        "limit": MAX_EVENTS,
        "user_count": dashboard["user_count"],
        "starts": dashboard["starts"],
        "completed": dashboard["completed"],
        "conversion": dashboard["conversion"],
        "rejected_users": dashboard["rejected_users"],
        "failed_users": dashboard["failed_users"],
        "failures": dashboard["failures"][:10],
        "stages": dashboard["stages"],
        "durations": dashboard["durations"],
    }
