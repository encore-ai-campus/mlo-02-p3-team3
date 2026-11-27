"""Run 중지 신호 · Run 스레드마다 하나 · 대기 · 트래픽 반복문이 확인 → 현재 Suite를 바로 끝냄"""
import threading
import time

_current_run = threading.local()


class RunCancelledError(Exception):
    """Run 중지 요청 · Suite 실행 중단용 · 실패가 아님"""


def bind_cancel_event(cancel_event: threading.Event | None) -> None:
    _current_run.cancel_event = cancel_event


def is_cancel_requested() -> bool:
    cancel_event = getattr(_current_run, "cancel_event", None)
    return cancel_event is not None and cancel_event.is_set()


def raise_if_cancelled() -> None:
    if is_cancel_requested():
        raise RunCancelledError("Run 중지 요청")


def cancellable_sleep(seconds: float) -> None:
    """중지 요청이 오면 남은 시간을 기다리지 않고 바로 중단 · Run 밖에서는 일반 sleep"""
    cancel_event = getattr(_current_run, "cancel_event", None)
    if cancel_event is None:
        time.sleep(seconds)
    elif cancel_event.wait(seconds):
        raise RunCancelledError("Run 중지 요청")
