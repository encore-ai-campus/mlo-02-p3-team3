"""시험 계정 사용 기록 · 시스템 초기화로 지워지지 않는 계정당 구매 한도 · 즉시 차단 대비 · Run 사이 유지(프로세스 공용)"""
import time
from collections.abc import Callable

PURCHASE_LIMIT_PER_ACCOUNT = 4  # 예매 웹 정책 · 회차당 계정 4매 · 초과 = PURCHASE_LIMIT + 5분 차단
SALE_CYCLE_SEC = 3600.0  # 판매 회차 1시간 · 지난 구매는 한도에서 제외
POLICY_BLOCK_SEC = 300.0  # 즉시 차단 5분


class TestAccountLedger:
    """계정 번호 순환 · 구매 매수 · 차단 시각 · 같은 계정 연속 사용 최소화"""
    __test__ = False  # pytest 수집 제외 · 이름이 Test로 시작

    def __init__(self, account_count: int, clock: Callable[[], float] = time.time) -> None:
        self.account_count = account_count
        self.clock = clock
        self.next_number = 1
        self.purchases_by_account: dict[int, list[tuple[float, int]]] = {}  # 계정 → [(구매 시각, 매수)]
        self.blocked_until_by_account: dict[int, float] = {}

    def purchased_seat_count(self, account_number: int, now: float) -> int:
        recent = [(at, seats) for at, seats in self.purchases_by_account.get(account_number, []) if now - at < SALE_CYCLE_SEC]
        self.purchases_by_account[account_number] = recent
        return sum(seats for _, seats in recent)

    def is_usable(self, account_number: int, seats_needed: int, now: float) -> bool:
        if self.blocked_until_by_account.get(account_number, 0.0) > now:
            return False
        return self.purchased_seat_count(account_number, now) + seats_needed <= PURCHASE_LIMIT_PER_ACCOUNT

    def take_usable(self, excluded: set[int], seats_needed: int) -> int | None:
        """다음 번호부터 한 바퀴 · 사용 중 · 한도 · 차단 계정 건너뜀 · 없으면 None"""
        now = self.clock()
        for offset in range(self.account_count):
            number = (self.next_number - 1 + offset) % self.account_count + 1
            if number not in excluded and self.is_usable(number, seats_needed, now):
                self.next_number = number % self.account_count + 1
                return number
        return None

    def count_usable(self, excluded: set[int], seats_needed: int) -> int:
        now = self.clock()
        return sum(1 for number in range(1, self.account_count + 1)
                   if number not in excluded and self.is_usable(number, seats_needed, now))

    def record_purchase(self, account_number: int, seat_count: int) -> None:
        if seat_count > 0:
            self.purchases_by_account.setdefault(account_number, []).append((self.clock(), seat_count))

    def record_block(self, account_number: int) -> None:
        self.blocked_until_by_account[account_number] = self.clock() + POLICY_BLOCK_SEC


SHARED_LEDGERS: dict[tuple[str, int], TestAccountLedger] = {}


def find_shared_ledger(base_url: str, account_count: int) -> TestAccountLedger:
    """대시보드 프로세스 공용 · Run마다 시스템 대상을 새로 만들어도 계정 기록 유지"""
    return SHARED_LEDGERS.setdefault((base_url.rstrip("/"), account_count), TestAccountLedger(account_count))
