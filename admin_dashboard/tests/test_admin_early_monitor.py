"""관리자 사전 관측 공개 읽기·무결성·인증·실제/시안 분리 검사."""
import hashlib
import json
from copy import deepcopy

import mongomock
import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.early_monitor.preview import build_preview
from ticket_guard_admin.early_monitor.reader import ObservationReadError, read, validate

NOW=2000000000
SCOPE='[true,null,null]'


def configured(monkeypatch):
    client=mongomock.MongoClient('mongodb://localhost/test',tz_aware=True)
    document=deepcopy(build_preview()['document'])
    document.update(asOf=NOW,windowStart=NOW-3600,windowEnd=NOW)
    document['series']=[dict(at=NOW,observed=True,detections=0,actions=document['actionCount'])]
    for metric in document.get('stageDetections',{}).values():
        for case in metric['cases']:
            case['at']=NOW
    payload=dict(contractVersion=1,asOf=NOW,ruleVersion=document['ruleVersion'],scopes={SCOPE:document},inputCount=1,ranges=[])
    version=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    client.test.early_versions.insert_one(dict(_id=version,version=version,data=payload))
    client.test.early_public.insert_one(dict(_id='pipeline',version=version,asOf=NOW))
    client.test.early_health.insert_one(dict(_id='pipeline',version=version,asOf=NOW,status='ready'))
    monkeypatch.setattr('ticket_guard_admin.early_monitor.reader.MongoClient',lambda *a,**k:client)
    return client,document


def test_reader_uses_single_verified_version_and_fails_closed(monkeypatch):
    client,document=configured(monkeypatch)
    assert read('mongodb://unused/test',SCOPE,now=NOW)['document']==document
    client.test.early_health.update_one({}, {'$set':{'status':'blocked'}})
    with pytest.raises(ObservationReadError): read('mongodb://unused/test',SCOPE,now=NOW)
    client.test.early_health.update_one({}, {'$set':{'status':'ready'}})
    with pytest.raises(ObservationReadError,match='지연'): read('mongodb://unused/test',SCOPE,now=NOW+121)
    client.test.early_versions.update_one({}, {'$set':{'data.scopes.'+SCOPE+'.signalCount':123}})
    with pytest.raises(ObservationReadError,match='버전'): read('mongodb://unused/test',SCOPE,now=NOW)


def test_missing_scope_or_inconsistent_counts_are_not_normal(monkeypatch):
    _,document=configured(monkeypatch)
    with pytest.raises(ObservationReadError): read('mongodb://unused/test','[false,null,null]',now=NOW)
    document['actionCount']=2
    with pytest.raises(ObservationReadError): validate(document)


def test_actual_preview_auth_and_no_sample_fallback(admin_settings,monkeypatch):
    client=TestClient(create_admin_dashboard_app(admin_settings))
    assert client.get('/').status_code==403
    assert client.get('/preview').status_code==403
    headers={'X-Admin-User':'manager'}
    preview=client.get('/preview',headers=headers)
    assert preview.status_code==200 and '단계별 탐지 현황' in preview.text
    assert '시안용 샘플 데이터' in preview.text
    def fail(*a,**k): raise ObservationReadError('observation-offline')
    monkeypatch.setattr('ticket_guard_admin.user_flow.pages.read_early',fail)
    actual=client.get('/',headers=headers)
    assert actual.status_code==200 and 'observation-offline' in actual.text
    assert '고정 샘플 · 실제 DB 연결 없음' not in actual.text
    assert actual.headers['cache-control']=='no-store'


@pytest.mark.parametrize('failed',[False,True])
def test_metric_structure_remains_visible_without_observations(admin_settings,monkeypatch,failed):
    def load(*a,**k):
        if failed:
            raise ObservationReadError('observation-offline')
        return None
    monkeypatch.setattr('ticket_guard_admin.user_flow.pages.read_early',load)
    client=TestClient(create_admin_dashboard_app(admin_settings))
    response=client.get('/',headers={'X-Admin-User':'manager'})
    assert response.status_code==200
    # 단계별 카드 영역만 검사한다. 기존 요약·추이·목록도 별도로 유지된다.
    section=response.text.split('aria-labelledby="early-title">',1)[1].split('<script type="application/json" data-sd-data>',1)[0]
    for label in ('로그인','예매 시작·대기열','좌석·인원 선택','예매 확정',
                  '반복 로그인 실패','로그인 제한 도달','오픈 전 반복 요청','반복 재입장',
                  '단계 우회 요청','선택 조건 위반',
                  '확정 요청 과다','응답 전 중첩','재요청 시도','구매 한도 초과 시도',
                  '탐지 항목 선택','탐지 시각 KST','실제 조치'):
        assert label in section
    assert 'sd-count">—' in section
    assert '<td>0</td>' not in section and 'None' not in section
    assert '미구현' not in section
    assert ('조회 실패' if failed else '미수집') in section
    assert '수신 요청(건)' not in section and '업무 사건(건)' not in section
    assert section.count('data-detection-stage=')==4
    assert not any(label in section for label in ('보안문자','오답 한도 도달','반복 오답','과도한 문제 변경'))
    assert '고정 샘플' not in section
    assert ('observation-offline' in section)==failed
