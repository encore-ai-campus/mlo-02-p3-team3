"""판정 수신 · 끝까지 진행한 세션만 비교 · Live = 판정이 올 때까지 대기 · 수신율 낮으면 탐지율 · 오탐률 측정 불가
판정 없음 = 정상으로 세면 판정을 하나도 못 받아도 오탐 0% · 탐지 0%가 나옴 → 수신율로 먼저 거름"""
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from ticket_guard_qa.domain.models import (
    REFERENCE_LINE_PREFIX,
    AnswerKey,
    SystemVerdicts,
    VirtualUserSessionResult,
)

VERDICT_WAIT_SEC = 90.0  # Live · 트래픽 끝 → 판정 저장 최대 대기 · Gold p95 약 10초 · 이상 탐지 갱신 여유
VERDICT_POLL_SEC = 5.0
MIN_VERDICT_COVERAGE_PERCENT = 90.0  # 끝까지 진행한 세션 중 판정을 받은 비율 · 미만 = 측정 불가


@dataclass(frozen=True)
class VerdictCollection:
    verdicts: SystemVerdicts
    judged_answer_key: AnswerKey  # 끝까지 진행한 세션 · 오류 · 즉시 차단 제외
    received_count: int
    waited_sec: float

    @property
    def judged_count(self) -> int:
        return len(self.judged_answer_key)

    @property
    def coverage_percent(self) -> float:
        return self.received_count / self.judged_count * 100 if self.judged_count else 0.0

    @property
    def is_measurable(self) -> bool:
        return self.judged_count > 0 and self.coverage_percent >= MIN_VERDICT_COVERAGE_PERCENT


def select_judged_answer_key(answer_key: AnswerKey, session_results: list[VirtualUserSessionResult]) -> AnswerKey:
    """시스템에 실제로 예매 시도가 남은 세션만 · failed- · timeout- 등 QA가 만든 키 제외"""
    completed_keys = {result.session_key for result in session_results if result.is_completed}
    return {key: actor_type for key, actor_type in answer_key.items() if key in completed_keys}


def count_received(judged_answer_key: AnswerKey, verdicts: SystemVerdicts) -> int:
    return sum(1 for key in judged_answer_key if key in verdicts)


def collect_verdicts(
    read_verdicts: Callable[[], SystemVerdicts], judged_answer_key: AnswerKey, waits_for_verdicts: bool,
    report_progress: Callable[[str], None], sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic, wait_sec: float = VERDICT_WAIT_SEC, poll_sec: float = VERDICT_POLL_SEC,
) -> VerdictCollection:
    """Live = 수신율 기준 넘거나 wait_sec 지날 때까지 다시 읽기 · Mock = 한 번"""
    started_at = clock()
    while True:
        verdicts = read_verdicts()
        received_count = count_received(judged_answer_key, verdicts)
        collection = VerdictCollection(verdicts, judged_answer_key, received_count, clock() - started_at)
        if not waits_for_verdicts or collection.is_measurable or not judged_answer_key or clock() - started_at >= wait_sec:
            return collection
        report_progress(f"판정 수신 {received_count}/{len(judged_answer_key)} · 대기")
        sleep(poll_sec)


def describe_verdict_collection(collection: VerdictCollection) -> str:
    if not collection.judged_count:
        return f"{REFERENCE_LINE_PREFIX}판정 비교 대상 없음 · 끝까지 진행한 세션 0 · 탐지율 · 오탐률 측정 불가"
    received = f"판정 수신 {collection.received_count}/{collection.judged_count} ({collection.coverage_percent:.0f}%)"
    if collection.is_measurable:
        return received
    return (f"{REFERENCE_LINE_PREFIX}{received} · 기준 {MIN_VERDICT_COVERAGE_PERCENT:.0f}% 미만 · 탐지율 · 오탐률 측정 불가"
            f" · {collection.waited_sec:.0f}초 대기 · 판정 저장 지연 또는 세션 키 불일치")


def describe_blocked_sessions(answer_key: AnswerKey, session_results: list[VirtualUserSessionResult]) -> str | None:
    """즉시 차단 세션 · 판정 비교 제외 · 유형별 수 · 정답표 공개 없이 숫자만"""
    blocked_keys = {result.session_key for result in session_results if result.is_blocked}
    blocked_by_type = Counter(answer_key[key] for key in blocked_keys if key in answer_key)
    blocked_count = sum(1 for result in session_results if result.is_blocked)
    if not blocked_count:
        return None
    human_count = sum(count for actor_type, count in blocked_by_type.items() if not actor_type.is_bot)
    return (f"{REFERENCE_LINE_PREFIX}시스템 즉시 차단 {blocked_count}세션 · 판정 비교 제외"
            f" · 봇 {sum(blocked_by_type.values()) - human_count} · 정상 {human_count}")
