"""첨부 시안용 고정 예시. 이 모듈은 preview 경로에서만 호출한다."""
from datetime import datetime, timedelta, timezone

from .presentation import display


def build_preview():
    end = datetime(2026, 11, 12, 5, 10, tzinfo=timezone.utc)
    at = (end-timedelta(minutes=7)).timestamp()
    timeline = [dict(at=at, kind='첫 구매 요청'), dict(at=at+1, kind='응답 전 후속 요청'), dict(at=at+2, kind='통신 실패')]
    document = dict(contractVersion=1, status='observed', windowStart=(end-timedelta(minutes=10)).isoformat(),
        windowEnd=end.isoformat(), policy=dict(overlap=True, retry=10, pages=10, fast=2, purchases=8, limit=1),
        counts=dict(overlap=1, retry=1, pages=1, fast=1, purchases=1, limit=1), attempts=2, subjects=4,
        evaluated=8, pending=0, unavailable=0, truncated=False,
        detections=[dict(_id='preview-a', eventId='a', occurredAt=at, user='A', attempt='sample-a',
                         label='응답 전 중첩', observed=1, rule='overlap', evidence=timeline),
                    dict(_id='preview-retry', eventId='a-retry', occurredAt=at+1, user='A', attempt='sample-a',
                         label='재요청 시도', observed=1, rule='retry', evidence=timeline),
                    dict(_id='preview-c', eventId='c', occurredAt=at, user='C', attempt='sample-c',
                         label='다중 페이지 계정', observed=2, rule='pages', evidence=timeline)],
        timelines={'a':timeline, 'a-retry':timeline, 'c':timeline},
        accounts=[dict(user=u, count=2 if u == 'B' else 1, quantity=2 if u == 'B' else 1,
            signals=[signal], bookings=[dict(id=f'TG-{u}{i}', quantity=1, show='시안 공연', schedule='시안 회차', at=at)
                                        for i in range(1,3 if u=='B' else 2)],
            evidence=timeline, durations=[.18] if u == 'B' else [])
            for u, signal in [('A','재응답'), ('B','단시간 복수 구매'), ('C','다중 페이지 관측'), ('D','경로 미확인')]],
        series=[dict(at=(end-timedelta(minutes=10-i)).isoformat(), overlap=int(i==3), retry=int(i==3), observed=True) for i in range(11)])
    result = display(document)
    result['notice'] = '시안용 샘플 데이터 · 모든 기준값은 화면 예시이며 운영 정책이 아닙니다.'
    return result
