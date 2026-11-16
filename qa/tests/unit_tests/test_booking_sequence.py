"""예매 순서 선택 · 봇 E 보안문자 반복 시도 · 봇 F 대기열 재진입 · 기존 봇 기본 순서 유지 · 실제 · 모의 대상 같은 이벤트"""
import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from ticket_guard_qa.domain.enums import ActorType, BookingOutcome, BookingSequence
from ticket_guard_qa.domain.models import (
    CAPTCHA_ATTEMPT_LIMIT,
    CAPTCHA_REFRESH_COUNT,
    CAPTCHA_WRONG_ANSWER_COUNT,
    QUEUE_REENTRY_COUNT,
    VirtualUserSessionPlan,
    find_sequence_steps,
)
from ticket_guard_qa.macro_bots.behavior_description import describe_behavior
from ticket_guard_qa.macro_bots.behavior_fields import BehaviorInputError, behavior_to_dict, parse_behavior_form
from ticket_guard_qa.macro_bots.bot_catalog import find_bot_type_info
from ticket_guard_qa.macro_bots.bot_profile_store import profile_from_document
from ticket_guard_qa.macro_bots.bot_profiles import DEFAULT_BEHAVIOR_BY_ACTOR_TYPE
from ticket_guard_qa.system_targets.fake_system import (
    CAPTCHA_REFRESH_EVENT_TYPE,
    CAPTCHA_WRONG_EVENT_TYPE,
    build_fake_system_target,
)
from ticket_guard_qa.system_targets.live_booking_client import BookingFlowError, LiveBookingClient

CAPTCHA_BOT_BEHAVIOR = DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_CAPTCHA]
REENTRY_BOT_BEHAVIOR = DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_REENTRY]
SEQUENCE_BOTS = (ActorType.BOT_CAPTCHA, ActorType.BOT_REENTRY)
CAPTCHA_ANSWER = "QA123"


def with_sequence(sequence: BookingSequence):
    return replace(CAPTCHA_BOT_BEHAVIOR, booking_sequence=sequence)


def test_existing_bots_keep_standard_sequence():
    for actor_type, behavior in DEFAULT_BEHAVIOR_BY_ACTOR_TYPE.items():
        if actor_type not in SEQUENCE_BOTS:
            assert behavior.booking_sequence == BookingSequence.STANDARD, actor_type
            assert not find_sequence_steps(behavior).captcha_retry_actions


def test_wrong_answers_stay_below_system_limit():
    steps = find_sequence_steps(CAPTCHA_BOT_BEHAVIOR)
    assert steps.captcha_wrong_answer_count == CAPTCHA_ATTEMPT_LIMIT - 1  # 5회째 = 시스템이 예매 취소
    assert steps.captcha_retry_actions == ("wrong_answer", "refresh") * CAPTCHA_REFRESH_COUNT


@pytest.mark.parametrize(("actor_type", "short_name", "category_title", "slug"), [
    (ActorType.BOT_CAPTCHA, "E", "보안문자 우회", "bot_captcha"),
    (ActorType.BOT_REENTRY, "F", "요청 자동화", "bot_reentry"),
])
def test_sequence_bots_are_listed_and_count_toward_macro_target(actor_type, short_name, category_title, slug):
    info = find_bot_type_info(actor_type)
    assert (info.short_name, info.category.title, info.detection_target_id) == (short_name, category_title, "bot_abd_detection")
    assert info.traffic_channel == "HTTP 트래픽"  # 예매 순서 선택 = HTTP 트래픽 봇만
    assert info.slug == slug


def test_version_saved_before_sequence_field_reads_as_standard():
    old_values = behavior_to_dict(DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_B])
    del old_values["booking_sequence"]  # 필드 추가 전 저장한 B2 버전
    profile = profile_from_document({"actor_type": "BOT_B", "version_number": 2, "profile": {"behavior": old_values},
                                     "saved_at": datetime(2026, 11, 10, tzinfo=timezone.utc)})
    assert profile.behavior.booking_sequence == BookingSequence.STANDARD


def test_settings_form_accepts_sequence_choice_and_rejects_unknown():
    form = {name: str(value) for name, value in behavior_to_dict(CAPTCHA_BOT_BEHAVIOR).items()}
    assert parse_behavior_form(form) == CAPTCHA_BOT_BEHAVIOR
    with pytest.raises(BehaviorInputError, match="범위 오류"):
        parse_behavior_form(form | {"booking_sequence": str(max(BookingSequence) + 1)})


def test_description_adds_captcha_line_only_for_retry_sequence():
    lines = {line.step_title: line.headline for line in describe_behavior(CAPTCHA_BOT_BEHAVIOR)}
    assert lines["보안문자 단계"] == f"보안문자 오답 {CAPTCHA_WRONG_ANSWER_COUNT}회 · 새로 받기 {CAPTCHA_REFRESH_COUNT}회 뒤 정답 입력"
    assert "보안문자 단계" not in [line.step_title for line in describe_behavior(with_sequence(BookingSequence.STANDARD))]


def test_description_adds_queue_line_only_for_reentry_sequence():
    lines = {line.step_title: line.headline for line in describe_behavior(REENTRY_BOT_BEHAVIOR)}
    assert lines["대기열 단계"] == f"대기열 진입 → 취소 {QUEUE_REENTRY_COUNT}회 반복 뒤 마지막 진입으로 진행"
    assert "대기열 단계" not in [line.step_title for line in describe_behavior(CAPTCHA_BOT_BEHAVIOR)]


ATTEMPT_BOUND_ACTIONS = {"heartbeat", "verify", "cancel", "confirm"}


class CaptchaRecordingServer:
    """예매 서버 흉내 · 요청 순서 기록 · 오답 = 400 CAPTCHA_WRONG · 제한 도달 = 409 CAPTCHA_LIMIT (web/services.verify)"""

    def __init__(self) -> None:
        self.steps: list[str] = []
        self.wrong_count = 0
        self.attempt_count = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/":
            return httpx.Response(200, headers=[("set-cookie", "csrftoken=token-1; Path=/")])
        if request.url.path == "/captcha.png":
            self.steps.append(f"captcha_image · {request.url.params.get('reason', '')}")
            return httpx.Response(200, content=b"png")
        if request.url.path == "/api/events/":
            return httpx.Response(202, json={"accepted": True})
        body = json.loads(request.content)
        action = body["action"]
        if action == "verify" and body["answer"] != CAPTCHA_ANSWER:
            self.wrong_count += 1
            self.steps.append("verify · wrong")
            if self.wrong_count >= CAPTCHA_ATTEMPT_LIMIT:
                return httpx.Response(409, json={"code": "CAPTCHA_LIMIT"})
            return httpx.Response(400, json={"code": "CAPTCHA_WRONG"})
        if action == "start":
            self.attempt_count += 1  # 진입마다 새 예매 시도 · web/services.start
        attempt_id = f"attempt-{self.attempt_count}"
        if action in ATTEMPT_BOUND_ACTIONS and body.get("attemptId") != attempt_id:  # 취소한 시도 재사용 = 거부
            return httpx.Response(409, json={"code": "STALE_ATTEMPT"})
        self.steps.append(action)
        response_body = {"start": {"id": attempt_id, "stage": "queue"}, "heartbeat": {"id": attempt_id, "stage": "captcha"},
                         "verify": {"stage": "select"}, "cancel": {"stage": "cancelled"},
                         "confirm": {"redirect": "/complete/TG-1/"}}.get(action, {})
        return httpx.Response(200, json=response_body)


def run_live_flow(behavior, server: CaptchaRecordingServer | None = None):
    server = server or CaptchaRecordingServer()
    client = LiveBookingClient("http://web:8000", httpx.MockTransport(server.handle))
    plan = VirtualUserSessionPlan(behavior, 0.0, (0.0, 0.0, 0.0), ("A1", "A2", "A3"))

    async def run():
        try:
            return await client.run_booking_flow("qa-user-0001", "qa-password", CAPTCHA_ANSWER, plan, time.monotonic(),
                                                 poll_interval_sec=0)
        finally:
            await client.close()

    return asyncio.run(run()), server.steps


def test_live_reentry_cancels_and_restarts_before_booking_with_last_attempt():
    server = CaptchaRecordingServer()
    result, steps = run_live_flow(REENTRY_BOT_BEHAVIOR, server)
    assert result.booking_outcome == BookingOutcome.BOOKED
    assert steps == ["login", "start", *["heartbeat", "cancel", "start"] * QUEUE_REENTRY_COUNT, "heartbeat",
                     "captcha_image · initial", *FLOW_END]
    assert result.booking_attempt_id == f"attempt-{QUEUE_REENTRY_COUNT + 1}"  # 정답표 = 마지막 시도


FLOW_START = ["login", "start", "heartbeat", "captcha_image · initial"]
FLOW_END = ["verify", "confirm"]


@pytest.mark.parametrize(("sequence", "retry_steps"), [
    (BookingSequence.STANDARD, []),
    (BookingSequence.CAPTCHA_REFRESH, ["captcha_image · user_refresh"] * CAPTCHA_REFRESH_COUNT),
    (BookingSequence.CAPTCHA_WRONG_ANSWER, ["verify · wrong"] * CAPTCHA_WRONG_ANSWER_COUNT),
    (BookingSequence.CAPTCHA_REFRESH_AND_WRONG_ANSWER,
     ["verify · wrong", "captcha_image · user_refresh"] * CAPTCHA_REFRESH_COUNT),
])
def test_live_sequence_sends_retries_before_answer_and_books(sequence, retry_steps):
    result, steps = run_live_flow(with_sequence(sequence))
    assert result.booking_outcome == BookingOutcome.BOOKED
    assert steps == FLOW_START + retry_steps + FLOW_END


def test_live_wrong_answer_reaching_limit_is_reported_as_error():
    server = CaptchaRecordingServer()
    server.wrong_count = 1  # 이전 시도 오답 1회 남음 → 4번째 오답에서 제한 도달
    with pytest.raises(BookingFlowError) as raised:
        run_live_flow(with_sequence(BookingSequence.CAPTCHA_WRONG_ANSWER), server)
    assert (raised.value.step, raised.value.error_code) == ("verify · wrong_answer", "CAPTCHA_LIMIT")


def test_mock_publishes_cancelled_attempts_with_own_attempt_ids():
    target = build_fake_system_target()
    result = target.booking_api.record_session(REENTRY_BOT_BEHAVIOR)
    cancelled = [record.event for record in target.booking_api.state.stream_records
                 if record.event is not None and record.event["eventType"] == "queue_cancel"]
    assert len(cancelled) == QUEUE_REENTRY_COUNT
    assert all(event["bookingAttemptId"] != result.session_key for event in cancelled)  # 취소한 시도 = 정답표 밖
    assert len({event["bookingAttemptId"] for event in cancelled}) == QUEUE_REENTRY_COUNT


def test_mock_publishes_same_retry_events_as_live():
    target = build_fake_system_target()
    state = target.booking_api.state

    def session_event_types(session_key: str) -> list[str]:
        return [record.event["eventType"] for record in state.stream_records
                if record.event is not None and record.key == session_key and record.event["eventType"] != "seat_select"]

    standard = target.booking_api.record_session(with_sequence(BookingSequence.STANDARD))
    retried = target.booking_api.record_session(CAPTCHA_BOT_BEHAVIOR)
    assert CAPTCHA_WRONG_EVENT_TYPE not in session_event_types(standard.session_key)
    retried_types = session_event_types(retried.session_key)
    assert retried_types.count(CAPTCHA_WRONG_EVENT_TYPE) == CAPTCHA_WRONG_ANSWER_COUNT
    assert retried_types.count(CAPTCHA_REFRESH_EVENT_TYPE) == CAPTCHA_REFRESH_COUNT
    assert retried_types.index("queue_admitted") < retried_types.index(CAPTCHA_WRONG_EVENT_TYPE)  # 입장 뒤 · 통과 전
    assert retried_types.index(CAPTCHA_REFRESH_EVENT_TYPE) < retried_types.index("captcha_success")
