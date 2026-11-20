"""기기 정보 변경 · 봇 G 기기 정보 위장 · 세션마다 다른 브라우저 정보 · 실제 · 모의 대상 같은 헤더"""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from ticket_guard_qa.domain.enums import ActorType, BrowserIdentity
from ticket_guard_qa.domain.models import VirtualUserSessionPlan
from ticket_guard_qa.domain.raw_event_routing import find_expected_raw_collection
from ticket_guard_qa.macro_bots.behavior_description import describe_behavior
from ticket_guard_qa.macro_bots.behavior_fields import BehaviorInputError, behavior_to_dict, parse_behavior_form
from ticket_guard_qa.macro_bots.bot_catalog import find_bot_type_info
from ticket_guard_qa.macro_bots.bot_profile_store import profile_from_document
from ticket_guard_qa.macro_bots.bot_profiles import DEFAULT_BEHAVIOR_BY_ACTOR_TYPE
from ticket_guard_qa.macro_bots.browser_profiles import ROTATING_BROWSER_PROFILES
from ticket_guard_qa.system_targets.fake_system import (
    FAKE_QA_REMOTE_ADDRESS,
    FAKE_SERVER_REQUEST_EVENT_TYPE,
    build_fake_system_target,
)
from ticket_guard_qa.system_targets.live_booking_client import (
    BROWSER_USER_AGENT,
    LiveBookingClient,
    choose_request_headers,
)
from ticket_guard_qa.test_suites.event_delivery import evaluate_delivery
from ticket_guard_qa.traffic_generators.virtual_user_traffic import build_session_plans

DEVICE_SPOOF_BEHAVIOR = DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_DEVICE_SPOOF]
HUMAN_BEHAVIOR = DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.HUMAN]
RANDOM_SEED = 20261116


def test_only_device_spoof_bot_rotates_browser_info():
    for actor_type, behavior in DEFAULT_BEHAVIOR_BY_ACTOR_TYPE.items():
        expected = BrowserIdentity.ROTATING if actor_type == ActorType.BOT_DEVICE_SPOOF else BrowserIdentity.FIXED
        assert behavior.browser_identity == expected, actor_type


def test_device_spoof_bot_is_listed_and_counts_toward_macro_target():
    info = find_bot_type_info(ActorType.BOT_DEVICE_SPOOF)
    assert (info.short_name, info.category.title, info.detection_target_id) == ("G", "세션 · 기기 위장", "bot_abd_detection")
    assert (info.traffic_channel, info.slug) == ("HTTP 트래픽", "bot_device_spoof")


def test_version_saved_before_identity_field_reads_as_fixed():
    old_values = behavior_to_dict(DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_A])
    del old_values["browser_identity"]  # 필드 추가 전 저장한 B2 버전
    profile = profile_from_document({"actor_type": "BOT_A", "version_number": 2, "profile": {"behavior": old_values},
                                     "saved_at": datetime(2026, 11, 16, tzinfo=timezone.utc)})
    assert profile.behavior.browser_identity == BrowserIdentity.FIXED


def test_settings_form_accepts_identity_choice_and_rejects_unknown():
    form = {name: str(value) for name, value in behavior_to_dict(DEVICE_SPOOF_BEHAVIOR).items()}
    assert parse_behavior_form(form) == DEVICE_SPOOF_BEHAVIOR
    with pytest.raises(BehaviorInputError, match="범위 오류"):
        parse_behavior_form(form | {"browser_identity": str(max(BrowserIdentity) + 1)})


def test_description_names_rotating_browser_info():
    trace_line = next(line for line in describe_behavior(DEVICE_SPOOF_BEHAVIOR) if line.step_title == "브라우저 흔적")
    assert "세션마다 다른 PC · 모바일 브라우저 정보" in trace_line.detail
    human_line = next(line for line in describe_behavior(HUMAN_BEHAVIOR) if line.step_title == "브라우저 흔적")
    assert human_line.detail == "일반 PC 브라우저와 같은 접속 정보"


def test_plans_give_rotating_sessions_varied_profiles_and_keep_others_unchanged():
    behaviors = [DEVICE_SPOOF_BEHAVIOR] * 20 + [HUMAN_BEHAVIOR] * 5
    plans = build_session_plans(behaviors, RANDOM_SEED)
    rotating_profiles = [plan.browser_profile for plan in plans[:20]]
    assert all(profile in ROTATING_BROWSER_PROFILES for profile in rotating_profiles)
    assert len({profile.profile_id for profile in rotating_profiles}) >= 4  # 세션마다 다른 기기처럼
    assert all(plan.browser_profile is None for plan in plans[20:])
    assert build_session_plans(behaviors, RANDOM_SEED) == plans  # 같은 시드 = 같은 배정


def test_profile_assignment_does_not_change_existing_plan_values():
    rotating = build_session_plans([DEVICE_SPOOF_BEHAVIOR] * 5, RANDOM_SEED)
    fixed = build_session_plans([replace(DEVICE_SPOOF_BEHAVIOR, browser_identity=BrowserIdentity.FIXED)] * 5, RANDOM_SEED)
    assert [replace(plan, browser_profile=None, behavior=fixed[0].behavior) for plan in rotating] == fixed


def test_same_device_windows_share_one_profile_and_browser_sessions_are_skipped():
    shared = replace(DEVICE_SPOOF_BEHAVIOR, sessions_per_device=4)
    plans = build_session_plans([shared] * 8 + [DEVICE_SPOOF_BEHAVIOR], RANDOM_SEED, [False] * 8 + [True])
    profile_by_device: dict[str, set[str]] = {}
    for plan in plans[:8]:
        profile_by_device.setdefault(plan.device_key, set()).add(plan.browser_profile.profile_id)
    assert len(profile_by_device) == 2 and all(len(ids) == 1 for ids in profile_by_device.values())
    assert plans[8].browser_profile is None  # 브라우저 트래픽 = 실제 브라우저 정보 그대로


def test_request_headers_follow_profile_then_browser_trace():
    profile = ROTATING_BROWSER_PROFILES[5]
    rotating_plan = VirtualUserSessionPlan(DEVICE_SPOOF_BEHAVIOR, 0.0, (0.0,), ("A1",), browser_profile=profile)
    assert choose_request_headers(rotating_plan) == {"User-Agent": profile.user_agent,
                                                     "Accept-Language": profile.accept_language}
    assert choose_request_headers(VirtualUserSessionPlan(HUMAN_BEHAVIOR, 0.0, (0.0,), ("A1",))) == {
        "User-Agent": BROWSER_USER_AGENT}
    assert choose_request_headers(VirtualUserSessionPlan(DEFAULT_BEHAVIOR_BY_ACTOR_TYPE[ActorType.BOT_A], 0.0, (0.0,),
                                                         ("A1",))) == {}


def test_live_client_sends_profile_headers_on_every_request():
    profile = ROTATING_BROWSER_PROFILES[6]
    seen_headers: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append((request.headers["User-Agent"], request.headers["Accept-Language"]))
        return httpx.Response(200, json={"ok": True})

    async def send_requests() -> None:
        client = LiveBookingClient("http://web", httpx.MockTransport(handler), request_headers=profile.to_request_headers())
        try:
            await client.http_client.get("/login/")
            await client.post_json("/api/action/", {"action": "heartbeat"})
        finally:
            await client.close()

    asyncio.run(send_requests())
    assert seen_headers == [(profile.user_agent, profile.accept_language)] * 2


def test_mock_records_server_request_with_profile_headers():
    target = build_fake_system_target(RANDOM_SEED)
    plans = build_session_plans([DEVICE_SPOOF_BEHAVIOR] * 3 + [HUMAN_BEHAVIOR], RANDOM_SEED)
    results = [asyncio.run(target.booking_api.run_virtual_user_session(plan, 0.0)) for plan in plans]
    records = target.event_stream_reader.read_since({}, "run-browser-identity")
    request_events = [record.event for record in records if record.event["eventType"] == FAKE_SERVER_REQUEST_EVENT_TYPE]

    assert [event["bookingAttemptId"] for event in request_events] == [result.session_key for result in results[:3]]
    for event, plan in zip(request_events, plans):
        headers = {header["name"]: header["value"] for header in event["details"]["request"]["headers"]}
        assert headers == plan.browser_profile.to_request_headers()
        assert event["details"]["request"]["remoteAddress"] == FAKE_QA_REMOTE_ADDRESS  # 접속 주소는 모두 같음
        assert find_expected_raw_collection(event) == "http_events"
    assert len({event["capture"]["anonymousId"] for event in request_events}) == 3  # 세션마다 새 기기 쿠키

    report = evaluate_delivery(results, records)
    assert report.capture_event_count == 3 and not report.missing_server_events
