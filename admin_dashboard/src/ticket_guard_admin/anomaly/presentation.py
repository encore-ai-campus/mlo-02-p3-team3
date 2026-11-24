"""운영 게시물·샘플을 동일 레이아웃으로 표시하되 오류 시 샘플을 사용하지 않는다."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
RULES = {'overlap': '응답 전 중첩', 'retry': '재요청 시도', 'pages': '다중 페이지 계정',
         'fast': '빠른 완료', 'purchases': '단시간 복수 구매', 'limit': '한도 초과'}


def display(document=None, error=None):
    data = document or dict(policy=dict.fromkeys(RULES), counts=dict.fromkeys(RULES), attempts=None,
        subjects=None, detections=[], accounts=[], timelines={}, series=[], evaluated=0, pending=0, unavailable=0,
        status='unpublished', truncated=False)
    data = dict(data)
    data['error'] = error
    data['notice'] = error or ('공개 결과 없음 · 판정 필드가 없는 과거 데이터는 정상으로 간주하지 않습니다.'
        if document is None else '필수 데이터 미수집 · 수신한 서버 관측 없음' if data['status'] == 'unobserved'
        else '서버 관측 기반 검토 신호 · 봇 확정 및 자동 제재 없음')
    data['period'] = '관측 기간 확인 필요'
    if document:
        start, end = (datetime.fromisoformat(data[k]).astimezone(KST) for k in ('windowStart', 'windowEnd'))
        data['period'] = f'{start:%m/%d %H:%M}–{end:%m/%d %H:%M} KST'
    available = document is not None and not error and data['status'] == 'observed'

    def card(key, label, unit):
        value = data.get(key) if key in ('attempts', 'subjects') else data['counts'].get(key)
        configured = (any(data['policy'].get(rule) is not None for rule in
                      (('overlap','retry','pages') if key == 'attempts' else ('fast','purchases','limit')))
                      if key in ('attempts','subjects') else data['policy'].get(key) is not None)
        if error:
            state = '조회 실패'
        elif document is None:
            state = '공개 결과 없음'
        elif not available:
            state = '필수 데이터 미수집'
        elif value is None:
            state = '정책 미설정' if not configured else '지연 도착 대기' if data['pending'] else '판정 근거 부족'
        else:
            state = '관측 범위'
        return dict(key=key, label=label, value=value if available else None, unit=unit, state=state)

    data['pre_cards'] = [card('attempts', '고유 감지 시도', '건'), card('overlap', RULES['overlap'], '건'),
                         card('retry', RULES['retry'], '건'), card('pages', RULES['pages'], '개 계정')]
    data['post_cards'] = [card('subjects', '검토 대상', '개 계정'), card('fast', RULES['fast'], '개 계정'),
                          card('purchases', RULES['purchases'], '개 계정'), card('limit', RULES['limit'], '개 계정')]
    data['policies'] = [dict(label=label, value='정책 미설정' if data['policy'].get(key) is None else
        '활성' if key == 'overlap' else f"최근 1시간 {data['policy'][key]}매" if key == 'limit' else
        f"{data['policy'][key]}초") for key, label in RULES.items()]
    return data
