"""기기 정보 변경 봇이 고르는 브라우저 정보 목록 · PC · 모바일 섞음 · 접속 주소 · 화면 크기는 바꾸지 않음"""
import random
from dataclasses import replace

from ticket_guard_qa.domain.enums import BrowserIdentity
from ticket_guard_qa.domain.models import BrowserProfile, VirtualUserSessionPlan

ROTATING_BROWSER_PROFILES: tuple[BrowserProfile, ...] = (
    BrowserProfile("windows_chrome",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                   "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"),
    BrowserProfile("windows_edge",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
                   "Edg/131.0.0.0",
                   "ko,en;q=0.9,en-US;q=0.8"),
    BrowserProfile("windows_firefox",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
                   "ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3"),
    BrowserProfile("mac_safari",
                   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 "
                   "Safari/605.1.15",
                   "ko-KR,ko;q=0.9"),
    BrowserProfile("mac_chrome",
                   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
                   "Safari/537.36",
                   "en-US,en;q=0.9,ko;q=0.8"),
    BrowserProfile("iphone_safari",
                   "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 "
                   "Mobile/15E148 Safari/604.1",
                   "ko-KR,ko;q=0.9"),
    BrowserProfile("android_chrome",
                   "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile "
                   "Safari/537.36",
                   "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"),
    BrowserProfile("android_samsung",
                   "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 "
                   "Chrome/122.0.0.0 Mobile Safari/537.36",
                   "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"),
)


def assign_browser_profiles(plans: list[VirtualUserSessionPlan], random_seed: int) -> list[VirtualUserSessionPlan]:
    """기기 정보 변경 · HTTP 트래픽 세션만 · 기기마다 하나 · 같은 기기 예매 창끼리 같은 정보 · 같은 시드 = 같은 배정"""
    random_source = random.Random(f"browser-profile-{random_seed}")  # 트래픽 시드와 분리 · 기존 계획 그대로
    profile_by_device_key: dict[str, BrowserProfile] = {}
    assigned = []
    for plan in plans:
        if plan.behavior.browser_identity != BrowserIdentity.ROTATING or plan.uses_browser:
            assigned.append(plan)
            continue
        profile = profile_by_device_key.get(plan.device_key)
        if profile is None:
            profile = profile_by_device_key[plan.device_key] = random_source.choice(ROTATING_BROWSER_PROFILES)
        assigned.append(replace(plan, browser_profile=profile))
    return assigned
