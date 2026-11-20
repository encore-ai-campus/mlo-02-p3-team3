"""용어집의 고정 값 · AGENTS.md 10장"""
from enum import IntEnum, StrEnum


class VerdictStage(StrEnum):
    NORMAL = "NORMAL"
    OBSERVE = "OBSERVE"
    CHALLENGE = "CHALLENGE"
    HOLD = "HOLD"

    @property
    def is_detected(self) -> bool:
        return self in (VerdictStage.CHALLENGE, VerdictStage.HOLD)  # 탐지 = CHALLENGE 이상


class ActorType(str):
    """봇 유형 id · 기본 9종 = 아래 상수 · 화면에서 추가한 유형 = QA DB bot_types · 전체 목록은 macro_bots/bot_catalog"""
    __slots__ = ()

    HUMAN: "ActorType"
    BOT_A: "ActorType"
    BOT_B: "ActorType"
    BOT_C: "ActorType"
    BOT_D: "ActorType"
    BOT_CAPTCHA: "ActorType"  # 보안문자 반복 시도 · 화면 추가 id(BOT_E …)와 겹치지 않게 이름 id
    BOT_REENTRY: "ActorType"  # 대기열 재진입 반복
    BOT_DEVICE_SPOOF: "ActorType"  # 세션마다 기기 정보 변경
    BOT_NEW: "ActorType"  # 새 유형 · 목표 없음 · 결과만 공개

    @property
    def value(self) -> str:
        return str.__str__(self)

    @property
    def is_bot(self) -> bool:
        return self.value != HUMAN_ACTOR_ID

    def __repr__(self) -> str:
        return f"ActorType({self.value!r})"


HUMAN_ACTOR_ID = "HUMAN"
ActorType.HUMAN = ActorType(HUMAN_ACTOR_ID)
ActorType.BOT_A = ActorType("BOT_A")
ActorType.BOT_B = ActorType("BOT_B")
ActorType.BOT_C = ActorType("BOT_C")
ActorType.BOT_D = ActorType("BOT_D")
ActorType.BOT_CAPTCHA = ActorType("BOT_CAPTCHA")
ActorType.BOT_REENTRY = ActorType("BOT_REENTRY")
ActorType.BOT_DEVICE_SPOOF = ActorType("BOT_DEVICE_SPOOF")
ActorType.BOT_NEW = ActorType("BOT_NEW")
BUILT_IN_ACTOR_TYPES: tuple[ActorType, ...] = (
    ActorType.HUMAN, ActorType.BOT_A, ActorType.BOT_B, ActorType.BOT_C, ActorType.BOT_D, ActorType.BOT_CAPTCHA,
    ActorType.BOT_REENTRY, ActorType.BOT_DEVICE_SPOOF, ActorType.BOT_NEW,
)


class SessionStep(StrEnum):
    """B3 종류별 진행 · 가상 사용자 한 명이 지나가는 단계"""
    ENTERED = "ENTERED"  # 예매 시작
    QUEUE_PASSED = "QUEUE_PASSED"
    CAPTCHA_PASSED = "CAPTCHA_PASSED"
    SEAT_SELECTED = "SEAT_SELECTED"
    CONFIRMED = "CONFIRMED"
    VERDICT_RECEIVED = "VERDICT_RECEIVED"


class BookingSequence(IntEnum):
    """예매 순서 · 행동 값 booking_sequence · 단계 차이는 domain/models.BOOKING_SEQUENCE_STEPS"""
    STANDARD = 0  # 로그인 → 대기열 → 보안문자 → 좌석 → 확정
    CAPTCHA_REFRESH = 1  # 보안문자 여러 번 새로 받은 뒤 입력
    CAPTCHA_WRONG_ANSWER = 2  # 제한 직전까지 오답 뒤 정답
    CAPTCHA_REFRESH_AND_WRONG_ANSWER = 3  # 오답 → 새로 받기 반복 뒤 정답
    QUEUE_REENTRY = 4  # 대기열 진입 → 취소 → 재진입 반복 뒤 진행


class BrowserIdentity(IntEnum):
    """기기 정보 · 행동 값 browser_identity · 브라우저 정보 목록은 macro_bots/browser_profiles"""
    FIXED = 0  # 브라우저 흔적 설정 기준 한 가지
    ROTATING = 1  # 세션마다 다른 브라우저 정보 · 같은 기기 예매 창끼리는 같음


class BookingOutcome(StrEnum):
    """VU 한 명의 예매 결과 · 오류는 VirtualUserSessionResult.is_error"""
    BOOKED = "BOOKED"
    SOLD_OUT = "SOLD_OUT"  # 남은 좌석 없음 · 오픈 경쟁의 정상 결과


class TargetKind(StrEnum):
    FAKE = "fake"
    LIVE = "live"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class SuiteOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # NotImplementedError · 실패 아님
    ERROR = "ERROR"


class Comparison(StrEnum):
    AT_MOST = "<="
    AT_LEAST = ">="


class AlertPolicy(StrEnum):
    NEVER = "never"
    ON_FAILURE = "on_failure"
    ALWAYS = "always"
