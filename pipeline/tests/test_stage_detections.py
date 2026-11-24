"""단계별 탐지 수는 일반 요청량과 분리하고 고유 시도·요청 단위를 보존한다."""
from early_monitor.events import project
from early_monitor.metrics import Policy, calculate
from user_flow.events import iso

NOW=2000000000
SCOPE='[false,null,null]'


def row(identity,kind,reason=None,attempt='a',request=None,at=NOW-10,action='confirm'):
    return project(dict(schemaVersion=1,eventId=identity,eventType=kind,occurredAt=iso(at),
        receivedAt=iso(at),source='server',userId='u',bookingAttemptId=attempt,showId='s',
        scheduleId='t',isSimulation=False,result='failure' if reason else 'success',errorCode=reason,
        details=dict(action=action,contextKnown=attempt is not None,observationRequestId=request)))


def test_normal_failures_and_refreshes_do_not_become_threshold_detections():
    rows=[row(str(i),'login_fail','INVALID_CREDENTIALS') for i in range(20)]
    rows += [row('wrong','captcha_wrong','CAPTCHA_WRONG'),row('refresh','captcha_refresh')]
    document=calculate(rows,NOW,Policy(),SCOPE)
    metrics=document['stageDetections']
    assert metrics['login_limit']['count']==0
    assert metrics['captcha_limit']['count']==0
    assert 'captcha_refresh_excess' not in metrics
    assert metrics['login_repeated_failure']['count'] is None  # 인증 실패만으로 새 서버 판정을 추측하지 않음
    assert metrics['login_repeated_failure']['cases']==[]


def test_captcha_limits_count_unique_attempts_and_keep_actions_separate():
    rows=[row('one','captcha_limit_reached','CAPTCHA_LIMIT',request='r1'),
          row('copy','captcha_limit_reached','CAPTCHA_LIMIT',request='r1'),
          row('next','captcha_limit_reached','CAPTCHA_LIMIT',request='r2')]
    metric=calculate(rows+rows,NOW,Policy(),SCOPE)['stageDetections']['captcha_limit']
    assert metric['count']==1  # 같은 예매 시도
    assert metric['eventCount']==2  # 서로 다른 수신 요청
    assert metric['actionCount']==2
    assert len(metric['cases'])==2


def test_counts_cover_all_cases_before_truncating_details():
    rows=[row(str(i),'booking_confirm_fail','SELECTION_LIMIT',request='r'+str(i)) for i in range(205)]
    metric=calculate(rows,NOW,Policy(),SCOPE)['stageDetections']['selection_condition']
    assert metric['count']==205 and metric['actionCount']==205
    assert len(metric['cases'])==200 and metric['truncated']


def test_confirm_excess_counts_attempts_not_every_over_threshold_request():
    rows=[row(str(i),'ticketing_action_received',request='r'+str(i),at=NOW-10+i*.1) for i in range(11)]
    metric=calculate(rows,NOW,Policy(),SCOPE)['stageDetections']['confirm_excess']
    assert metric['count']==1 and metric['eventCount']==2
    assert metric['actionCount'] is None
    assert all(case['reason']=='CONFIRM_REQUEST_EXCESS' for case in metric['cases'])


def test_absent_or_unidentified_targets_are_not_zero():
    assert calculate([],NOW,Policy(),SCOPE)['stageDetections']['captcha_limit']['count'] is None
    metrics=calculate([row('unknown','captcha_limit_reached','CAPTCHA_LIMIT',attempt=None)],NOW,Policy())['stageDetections']
    assert metrics['captcha_limit']['count'] is None and metrics['captcha_limit']['unknownTargets']==1
