"""실제 DB 오류 경로에서는 호출하지 않는 명시적 고정 시안."""
from datetime import datetime
import hashlib
import json

from .presentation import KST, display


def build_preview():
    now = datetime(2026, 11, 23, 16, tzinfo=KST).timestamp()
    selected = '[true,null,null]'
    stages = [dict(key=key, label=label, requests=value, events=value,
                   attempts=1, accounts=1, outcomes={kind:value})
              for key,label,value,kind in [('login','로그인',1,'login_success'),('start','예매 시작',1,'queue_enter'),
                   ('queue','대기열',1,'queue_enter'),('captcha','보안문자',2,'captcha_wrong'),
                   ('confirm','예매 확정',1,'booking_confirm_success')]]
    document = dict(contractVersion=1, scope=selected, asOf=now,
        windowStart=now-3600, windowEnd=now, evaluationSeconds=7200,
        policy=dict(enabled=True, seconds=3, count=10, mode='observe'), ruleVersion='preview-only',
        status='observed', stages=stages, signalCount=0, signalAttempts=0, detections=[], contexts={},
        actionCount=0, actionAttempts=0, actionSubjects=0, actionCounts={}, actions=[],
        durations=[dict(key=key, label=label, samples=1, unavailable=0, mean=value, median=value, p95=value)
                   for key,label,value in [('queue','대기열 진입 → 입장 허용',12),
                       ('captcha','입장 허용 → 보안문자 성공',8),('selection','보안문자 성공 → 첫 확정 요청 수신',20),
                       ('purchase','확정 요청 수신 → 구매 성공',.2)]],
        series=[dict(at=now,observed=True,detections=0,actions=0)], truncated=False,
        unlinkedActions=0, unknownContext=0, coverage='고정 샘플 · 실제 DB 연결 없음', fastPolicy='not_added')
    document['policy']['rule'] = 'confirm-receipt-count-v1'
    document['ruleVersion'] = hashlib.sha256(json.dumps(document['policy'],sort_keys=True).encode()).hexdigest()[:20]
    document['stageDetections'] = {key:dict(count=0,eventCount=0,actionCount=0,unknownTargets=0,
        truncated=False,cases=[]) for key in ('login_limit','duplicate_progress','captcha_limit',
                                            'step_bypass','selection_condition','confirm_excess')}
    case=dict(eventId='preview-captcha-limit',at=now,user='시안 계정',attempt='시안 예매 시도',
              reason='CAPTCHA_LIMIT',evidence=['preview-captcha-limit'],scope=selected)
    document['stageDetections']['captcha_limit'].update(count=1,eventCount=1,actionCount=1,cases=[case])
    document['stages'][3]['outcomes']={'captcha_wrong':1,'captcha_limit_reached':1}
    document.update(actionCount=1,actionAttempts=1,actionSubjects=1,actionCounts={'시도 취소':1},
        actions=[dict(id='preview-action',at=now,user='시안 계정',attempt='시안 예매 시도',
                      label='시도 취소',reason='CAPTCHA_LIMIT',ruleVersion='captcha-limit-v1',
                      threshold=5,observed=5,scope=selected)])
    document['series'][0]['actions']=1
    return display(dict(document=document, scopes=[selected], version='preview-only'), selected=selected)
