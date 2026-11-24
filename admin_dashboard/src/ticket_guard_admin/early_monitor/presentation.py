"""실제·시안 자료의 표시 변환. 실제 조회 실패를 샘플로 대체하지 않는다."""
import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
LABELS = {
 'login_failed':'인증 실패', 'login_locked':'잠금 거절', 'booking_access_rejected':'접근 거절',
 'login_success':'성공', 'login_fail':'실패·잠금 사건',
 'queue_enter':'진입', 'booking_not_open':'오픈 전 거절', 'queue_register':'등록',
 'queue_admitted':'입장 허용', 'queue_cancel':'취소', 'connection_timeout':'만료',
 'captcha_success':'성공', 'captcha_wrong':'오답', 'captcha_limit_reached':'한도 도달',
 'captcha_refresh':'문제 변경', 'booking_confirm_request':'업무 수락',
 'booking_confirm_success':'성공', 'booking_confirm_fail':'업무 실패',
 'booking_confirm_rejected':'요청 거절', 'booking_confirm_replayed':'기존 구매 재응답',
}

STAGE_FIELDS = (
 ('login','로그인',('login_success','login_failed','login_locked')),
 ('start','예매 시작',('queue_enter','booking_not_open','booking_access_rejected')),
 ('queue','대기열',('queue_enter','queue_register','queue_admitted','queue_cancel','connection_timeout','booking_access_rejected')),
 ('captcha','보안문자',('captcha_success','captcha_wrong','captcha_limit_reached','captcha_refresh','booking_access_rejected')),
 ('confirm','예매 확정',('booking_confirm_request','booking_confirm_success','booking_confirm_fail','booking_confirm_rejected','booking_confirm_replayed')),
)
ACTION_LABELS = ('시도 취소','로그인 제한','단계 접근 거절','구매 요청 거절',
                 '중복 페이지 거절','이전 시도 거절','이전 요청 거절','수량·좌석 요청 거절',
                 '기존 구매 재응답','오픈 전 시작 거절','기존 예매 진행 보호','인증 필요 요청 거절','진행 권한 만료')
DURATION_FIELDS = (('queue','대기열 진입 → 입장 허용'),('captcha','입장 허용 → 보안문자 성공'),
                   ('selection','보안문자 성공 → 첫 확정 요청 수신'),('purchase','확정 요청 수신 → 구매 성공'))


def empty_layout():
    """지표 정의만 유지한다. 실제 관측값·정책·표본 수를 만들어 넣지 않는다."""
    return dict(stages=[dict(key=key,label=label,requests=None,events=None,attempts=None,accounts=None,
                            outcomes=dict.fromkeys(fields)) for key,label,fields in STAGE_FIELDS],
                policy=dict(enabled=None,seconds=None,count=None),ruleVersion=None,
                signalCount=None,signalAttempts=None,detections=[],contexts={},
                actionCount=None,actionAttempts=None,actionSubjects=None,unlinkedActions=None,
                actionCounts={},actions=[],unknownContext=None,unknownQueueStage=None,
                durations=[dict(key=key,label=label,samples=None,unavailable=None,mean=None,median=None,p95=None)
                           for key,label in DURATION_FIELDS],series=[],truncated=False,
                coverage='최근 1시간 지표 · 수신 요청·업무 사건·고유 대상·실제 조치 분리')


def stamp(value):
    return datetime.fromtimestamp(value, KST).strftime('%m/%d %H:%M:%S')


def scope_label(value):
    simulation, show, schedule = json.loads(value)
    prefix = '시뮬레이션' if simulation is True else '실제' if simulation is False else '구분 미확인'
    return f'{prefix} · {show or "전체 공연"} · {schedule or "전체 회차"}'


def display(result=None, error=None, selected='[false,null,null]', previous_as_of=None):
    if result is None:
        previous = None
        if previous_as_of is not None:
            try:
                previous = stamp(previous_as_of)
            except (TypeError,ValueError,OverflowError,OSError):
                pass
        return dict(document=empty_layout(), available=False, error=error, selected=selected,
                    scopes=[dict(value=s,label=scope_label(s)) for s in
                            ('[false,null,null]','[true,null,null]','[null,null,null]')],
                    previous_as_of=previous,
                    period='조회 기간 —',as_of='—',version=None,labels=LABELS,
                    times={},series_times={},action_cards=[dict(label=label,value=None) for label in ACTION_LABELS],
                    notice=error or '사전 관측 공개 결과 없음 · 미수집을 0으로 표시하지 않음')
    document = result['document']
    return dict(document=document, available=True, error=error, selected=selected,
                scopes=[dict(value=s, label=scope_label(s)) for s in result['scopes']],
                period=f"{stamp(document['windowStart'])}–{stamp(document['windowEnd'])} KST",
                as_of=stamp(document['asOf']), version=result['version'],
                labels=LABELS, notice='서버 판정·5분 제한 관측 · 확정 요청 과다는 관찰 전용 · 탐지·제한 적용·제한 중 거절 분리',
                action_cards=[dict(label=label,value=document['actionCounts'].get(label,0)
                                   if document['actionCount'] is not None else None)
                              for label in ACTION_LABELS],
                series_times={row['at']:stamp(row['at']) for row in document['series']},
                times={row['id']:stamp(row['at']) for row in document['detections']+document['actions']})
