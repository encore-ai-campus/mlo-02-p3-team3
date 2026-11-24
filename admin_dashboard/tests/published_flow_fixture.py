"""
[홍석환] 공개 지표 계약 검사용 격리 자료
실제 저장 문서와 동일한 구조의 고정 자료를 만들며 운영 DB에는 쓰지 않는다.
"""
from datetime import datetime, timedelta, timezone


def published_documents(now=None, scope='all', version='test-version-1'):
    now = now or datetime.now(timezone.utc)
    def at(delta):
        return (now + timedelta(seconds=delta)).isoformat()
    metrics = dict(contractVersion=1, calculationVersion=1, countingUnit='browser_session',
                   asOf=at(0), windowStart=at(-43200), presenceTimeoutSeconds=30,
                   current=3, booking={'numerator': 2, 'denominator': 4, 'ratio': .5},
                   nodes=[dict(id='other', label='기타·미확인', order=6, current=1, entered=3, ongoing=1,
                               dwell={'samples': 0, 'meanSeconds': None, 'medianSeconds': None}),
                          dict(id='queue', label='대기열', order=2, current=2, entered=4, ongoing=2,
                               dwell={'samples': 2, 'meanSeconds': 5.5, 'medianSeconds': 4.5})],
                   edges=[dict(source='other', target='queue', kind='backward', moves=1, numerator=1, denominator=3, ratio=1/3, ongoing=1),
                          dict(source='queue', target='other', kind='forward', moves=2, numerator=2, denominator=4, ratio=.5, ongoing=2),
                          dict(source='queue', target='queue', kind='retry', moves=1, numerator=1, denominator=4, ratio=.25, ongoing=2),
                          dict(source='queue', target='cancelled', kind='cancel', moves=1, numerator=1, denominator=4, ratio=.25, ongoing=2)])
    current = {'_id': scope, 'version': version, 'asOf': now, 'status': 'verified',
               'result': {'_id': version, 'scope': scope, 'status': 'verified',
                          'verifiedAt': now, 'publishedAt': now, 'metrics': metrics}}
    series = {'_id': scope, 'version': version, 'asOf': now, 'status': 'verified',
              'result': {'_id': scope+':'+version, 'version': version, 'scope': scope, 'publishedAt': now,
                         'presence': [dict(at=at(-46800), current=99, observed=True),
                                      dict(at=at(-300), current=0, observed=True),
                                      dict(at=at(-60), current=None, observed=False),
                                      dict(at=at(0), current=3, observed=True)],
                         'booking': [dict(at=at(-300), numerator=0, denominator=0, ratio=None, ongoing=0, observed=True),
                                     dict(at=at(-60), numerator=None, denominator=None, ratio=None, ongoing=None, observed=False),
                                     dict(at=at(0), numerator=2, denominator=4, ratio=.5, ongoing=2, observed=True)]}}
    return current, series
