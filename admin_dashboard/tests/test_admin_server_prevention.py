"""All five server policy metrics reach the authenticated real page via a verified version."""
import json

import mongomock
import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.early_monitor.reader import read, ObservationReadError, validate

NOW=2000000000
REAL='[false,null,null]'


def publication():
    from early_monitor.events import project
    from early_monitor.store import observe, refresh
    from early_monitor.server_policies import RULES, VERSION
    from user_flow.events import iso
    client=mongomock.MongoClient('mongodb://localhost/target',tz_aware=True)
    for offset,rule in enumerate(RULES):
        seconds,threshold=RULES[rule]
        for kind in ('prevention_detected','prevention_applied','prevention_rejected'):
            details=dict(ruleId=rule,ruleVersion=VERSION,policyMode='temporary_restriction',
                threshold=threshold,windowSeconds=seconds,observed=5 if rule=='purchase_limit_attempt' else threshold,
                restrictionId=rule,restrictionScope='account' if rule=='login_repeated_failure' else 'account_show_schedule',
                observationRequestId=rule,appliedAt=NOW-10,expiresAt=NOW+290,
                purchasedQuantity=3,requestedQuantity=2,evidence=[rule+'prevention_detected'])
            value=dict(eventId=rule+kind,eventType=kind,occurredAt=iso(NOW-10),source='server',
                userId='account-'+rule,bookingAttemptId=None if rule=='login_repeated_failure' else 'attempt-'+rule,
                showId=None if rule=='login_repeated_failure' else 'show',scheduleId=None if rule=='login_repeated_failure' else 'schedule',
                isSimulation=False,result='success',errorCode=None,details=details)
            observe(client.target,project(value),dict(source='isolated',topic='events',partition=0,offset=offset))
    refresh(client.target,NOW)
    return client


def test_all_five_real_metrics_evidence_actions_and_existing_screen_are_preserved(admin_settings,monkeypatch):
    client=publication()
    monkeypatch.setattr('ticket_guard_admin.early_monitor.reader.MongoClient',lambda *a,**k:client)
    result=read('mongodb://unused/target',REAL,now=NOW)
    monkeypatch.setattr('ticket_guard_admin.user_flow.pages.read_early',lambda *a,**k:result)
    browser=TestClient(create_admin_dashboard_app(admin_settings))
    assert browser.get('/').status_code==403
    response=browser.get('/',headers={'X-Admin-User':'manager'})
    assert response.status_code==200
    fragment=response.text.split('<script type="application/json" data-sd-data>',1)[1].split('</script>',1)[0]
    metrics={item['key']:item for stage in json.loads(fragment) for item in stage['metrics']}
    for rule in ('login_repeated_failure','not_open_repeat','reentry_repeat','selection_repeat','purchase_limit_attempt'):
        metric=metrics[rule]
        assert metric['value']==metric['action_count']==metric['rejected_count']==1
        assert metric['action_subjects']==1 and metric['unit']=='건'
        assert metric['state']=='관측 범위'
        assert '5분 제한 적용' in metric['cases'][0]['action_status']
        assert metric['cases'][0]['evidence'] and metric['cases'][0]['user'].startswith('account-')
        assert '미구현' not in metric['state']
    assert not any(key.startswith('captcha') for key in metrics)
    assert response.text.count('data-detection-stage=')==4
    for title in ('요청·응답 추이','최근 감지 목록','계정 타임라인','계정별 구매 사례'):
        assert title in response.text
    assert '시안용 샘플 데이터' not in response.text


def test_bad_policy_version_and_stale_publication_are_rejected(monkeypatch):
    client=publication()
    monkeypatch.setattr('ticket_guard_admin.early_monitor.reader.MongoClient',lambda *a,**k:client)
    result=read('mongodb://unused/target',REAL,now=NOW)
    result['document']['stageDetections']['selection_repeat']['ruleVersion']='incorrect'
    with pytest.raises(ObservationReadError):
        validate(result['document'])
    with pytest.raises(ObservationReadError,match='지연'):
        read('mongodb://unused/target',REAL,now=NOW+121)
