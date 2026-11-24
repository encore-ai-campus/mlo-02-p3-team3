"""Server decision -> raw-preserved observation -> verified publication contract."""
import json
from copy import deepcopy
from types import SimpleNamespace

import mongomock
import pytest

from early_monitor.events import project
from early_monitor.metrics import Policy, calculate
from early_monitor.server_policies import RULES, VERSION
from early_monitor.store import observe, refresh
from user_flow.events import iso
from user_flow.verification import verified_record

NOW=2000000000
REAL='[false,null,null]'


def event(rule, kind='prevention_detected', identity=None, **overrides):
    seconds, threshold=RULES[rule]
    identity=identity or rule+':'+kind
    data=dict(ruleId=rule,ruleVersion=VERSION,policyMode='temporary_restriction',
              threshold=threshold,windowSeconds=seconds,observed=threshold,
              restrictionId='lock-'+rule,restrictionScope='account' if rule=='login_repeated_failure' else 'account_show_schedule',
              observationRequestId='request-'+rule)
    if rule=='purchase_limit_attempt':
        data.update(observed=5,purchasedQuantity=3,requestedQuantity=2)
    if kind in ('prevention_applied','prevention_rejected','prevention_released'):
        data.update(appliedAt=NOW-20,expiresAt=NOW+280)
    if kind=='prevention_applied':
        data['evidence']=[rule+':prevention_detected']
    if kind=='prevention_released':
        data.update(releasedAt=NOW-10,releaseReason='opened')
    result=dict(schemaVersion=1,eventId=identity,eventType=kind,occurredAt=iso(NOW-10 if kind=='prevention_released' else NOW-20),
        receivedAt=iso(NOW-20),source='server',userId='u',bookingAttemptId=None if rule=='login_repeated_failure' else 'a',
        showId=None if rule=='login_repeated_failure' else 's',scheduleId=None if rule=='login_repeated_failure' else 't',
        isSimulation=False,result='success',errorCode=None,details=data)
    result.update(overrides)
    return result


def all_events():
    return [event(rule,kind) for rule in RULES for kind in ('prevention_evaluated','prevention_detected','prevention_applied','prevention_rejected')]


def test_all_five_counts_actions_rejections_order_and_duplicates_are_independent():
    rows=[project(item) for item in all_events()]
    result=calculate(rows,NOW,Policy(),REAL)
    reverse=calculate(list(reversed(rows))+rows,NOW,Policy(),REAL)
    assert result==reverse
    for rule in RULES:
        metric=result['stageDetections'][rule]
        assert metric['count']==metric['actionCount']==metric['rejectedCount']==1
        assert metric['actionSubjects']==1
        assert metric['cases'][0]['observed']>=metric['threshold']
        assert metric['cases'][0]['actionStatus']=='active'
    assert result['actionCount']==5
    assert result['signalCount'] is None  # New restrictions are not confirm-observation signals.
    assert result['stageDetections']['login_limit']['count']==1


def test_late_application_and_release_correct_detail_without_inventing_actions():
    rule='not_open_repeat'
    detected=project(event(rule))
    result=calculate([detected],NOW,Policy(),REAL)['stageDetections'][rule]
    assert result['count']==1 and result['actionCount'] is None
    assert result['cases'][0]['actionStatus']=='unconfirmed'
    rows=[detected,project(event(rule,'prevention_applied')),project(event(rule,'prevention_released'))]
    document=calculate(rows,NOW,Policy(),REAL)
    corrected=document['stageDetections'][rule]
    assert corrected['count']==corrected['actionCount']==1
    assert corrected['cases'][0]['actionStatus']=='released'
    assert corrected['cases'][0]['releaseReason']=='opened'
    assert len(corrected['cases'][0]['evidence'])==3
    assert document['actions'][0]['releaseStatus']=='opened'
    assert document['actions'][0]['releasedAt']==NOW-10


def test_policy_scope_and_unknown_observation_are_never_zero_filled():
    assert calculate([],NOW,Policy(),REAL)['stageDetections']['reentry_repeat']['count'] is None
    rule='reentry_repeat'
    rows=[project(event(rule,isSimulation=True))]
    assert calculate(rows,NOW,Policy(),REAL)['stageDetections'][rule]['count'] is None
    assert calculate(rows,NOW,Policy(),'[true,null,null]')['stageDetections'][rule]['count']==1
    assert calculate(rows,NOW,Policy(),'[false,"other","t"]')['stageDetections'][rule]['count'] is None
    value=event(rule,'prevention_evaluated')
    value['details']['observed']=9
    evaluated=project(value)
    assert calculate([evaluated],NOW,Policy(),REAL)['stageDetections'][rule]['count']==0
    missing=project(event(rule,'prevention_evaluated'))
    pending=calculate([missing],NOW,Policy(),REAL)['stageDetections'][rule]
    assert pending['count'] is pending['eventCount'] is pending['actionCount'] is None


def test_malformed_policy_and_conflicting_restriction_fail_closed():
    value=event('selection_repeat')
    value['details']['threshold']=9
    with pytest.raises(ValueError):
        project(value)
    value=event('purchase_limit_attempt')
    value['details']['requestedQuantity']=1
    with pytest.raises(ValueError):
        project(value)
    first=project(event('reentry_repeat'))
    second=project(event('reentry_repeat',identity='conflicting',userId='other'))
    with pytest.raises(ValueError):
        calculate([first,second],NOW,Policy(),REAL)
    release=project(event('not_open_repeat','prevention_released',userId='other'))
    with pytest.raises(ValueError):
        calculate([project(event('not_open_repeat')),release],NOW,Policy(),REAL)


def test_submicrosecond_clock_encoding_does_not_reject_real_server_events():
    value=event('reentry_repeat','prevention_applied')
    at=NOW-20+0.1234567
    value['occurredAt']=iso(at)
    value['details'].update(appliedAt=at,expiresAt=at+300)
    assert project(value)['policy']['appliedAt']==at
    value['details']['appliedAt']-=1
    value['details']['expiresAt']-=1
    with pytest.raises(ValueError):
        project(value)


def test_one_hour_display_two_hour_history_and_old_version_no_fabricated_counts():
    old=event('login_repeated_failure',occurredAt=iso(NOW-3601))
    assert calculate([project(old)],NOW,Policy(),REAL)['stageDetections']['login_repeated_failure']['count'] is None
    current=event('login_repeated_failure',occurredAt=iso(NOW-3600))
    assert calculate([project(current)],NOW,Policy(),REAL)['stageDetections']['login_repeated_failure']['count']==1


def test_raw_fallback_bytes_reprocessing_partial_failure_and_version_consistency(monkeypatch):
    # Preserve the existing consumer routing fingerprint and fallback collection.
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'raw_consumer'))
    from router import route, VERSION as ROUTING_VERSION
    db=mongomock.MongoClient(tz_aware=True).target
    raw=mongomock.MongoClient().raw
    for offset,value in enumerate(all_events()):
        wire=json.dumps(value).encode()
        collection,meta=route(wire)
        assert collection=='unclassified_events' and ROUTING_VERSION=='raw-routing-v1'
        origin=dict(source='test',topic='events',partition=0,offset=offset)
        # verified_record checks the original byte representation, never just JSON equality.
        raw[collection].insert_one(dict(_id=str(offset),**origin,value=wire))
        assert verified_record(raw,'test','events',0,offset,wire)==wire
        observe(db,project(value),origin)
        observe(db,project(value),origin)
    refresh(db,NOW)
    pointer=db.early_public.find_one({'_id':'pipeline'})
    wrapper=db.early_versions.find_one({'_id':pointer['version']})
    assert wrapper['data']['inputCount']==len(all_events())
    assert wrapper['data']['scopes'][REAL]['stageDetections']['purchase_limit_attempt']['count']==1
    refresh(db,NOW)
    assert db.early_public.find_one({'_id':'pipeline'})==pointer
    monkeypatch.setattr(db.early_versions,'update_one',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('partial save')))
    with pytest.raises(RuntimeError):
        refresh(db,NOW+1)
    assert db.early_public.find_one({'_id':'pipeline'})==pointer
    assert db.early_health.find_one({'_id':'pipeline'})['status']=='blocked'


def test_real_flow_batch_entry_publishes_server_decisions_without_affecting_presence(tmp_path):
    from configuration.user_flow import FlowConfig
    from user_flow.runtime import Processor
    db=mongomock.MongoClient(tz_aware=True)
    config=FlowConfig('unused','events','isolated','unused','unused','isolated',str(tmp_path))
    config.raw_wait_seconds=0
    processor=Processor(config,db.raw,db.metrics,clock=lambda:NOW)
    records=[]
    for offset,value in enumerate(all_events()):
        row=SimpleNamespace(topic='events',partition=0,offset=offset,value=json.dumps(value).encode())
        records.append(row)
        db.raw.unclassified_events.insert_one(dict(source='isolated',topic='events',partition=0,offset=offset,value=row.value))
    frame=SimpleNamespace(toLocalIterator=lambda **kwargs:iter(records))
    processor.batch(frame,0)
    pointer=db.metrics.early_public.find_one()
    document=db.metrics.early_versions.find_one({'_id':pointer['version']})['data']['scopes'][REAL]
    assert all(document['stageDetections'][rule]['count']==1 for rule in RULES)
    state=processor.state()
    try:
        assert state.observations()==[]
    finally:
        state.close()
