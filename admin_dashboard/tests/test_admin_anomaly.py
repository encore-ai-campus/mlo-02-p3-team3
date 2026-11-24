"""운영 조회 상태와 시안 분리·인증·표시 계약."""
from datetime import datetime, timezone, timedelta

import mongomock
import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.anomaly.preview import build_preview
from ticket_guard_admin.anomaly.reader import read_anomaly, AnomalyReadError
from ticket_guard_admin.anomaly.presentation import display
from ticket_guard_admin.app import create_admin_dashboard_app


def test_unset_missing_and_failed_states_are_distinct():
    assert display()['pre_cards'][0]['value'] is None
    assert display(error='조회 실패')['pre_cards'][0]['state'] == '조회 실패'
    data = build_preview()
    data['policy']['fast'] = None
    data['counts']['fast'] = None
    assert display(data)['post_cards'][1]['state'] == '정책 미설정'
    data['status'] = 'unobserved'
    assert display(data)['post_cards'][0]['state'] == '필수 데이터 미수집'


def test_read_publication_stale_blocked_and_scope(monkeypatch):
    import ticket_guard_admin.anomaly.reader as reader
    client = mongomock.MongoClient('mongodb://unused/test', tz_aware=True)
    monkeypatch.setattr(reader, 'MongoClient', lambda *a, **k: client)
    assert read_anomaly('mongodb://unused/test') is None
    now = datetime.now(timezone.utc)
    doc = build_preview() | dict(_id='all', scope='all', asOf=now.isoformat(), ruleVersion='version')
    client.test.anomaly_public.insert_one(doc)
    client.test.anomaly_health.insert_one(dict(_id='ingest', status='ready', asOf=now, ruleVersion='version'))
    assert read_anomaly('mongodb://unused/test', now=now)['scope'] == 'all'
    assert read_anomaly('mongodb://unused/test', scope='missing', now=now) is None
    with pytest.raises(AnomalyReadError, match='오래된'):
        read_anomaly('mongodb://unused/test', now=now+timedelta(seconds=130))
    client.test.anomaly_health.update_one({}, {'$set': {'status':'blocked'}})
    with pytest.raises(AnomalyReadError, match='공개 보류'):
        read_anomaly('mongodb://unused/test', now=now)


def test_preview_two_modes_auth_and_live_does_not_substitute_sample(admin_settings):
    client = TestClient(create_admin_dashboard_app(admin_settings))
    assert client.get('/preview').status_code == 403
    preview = client.get('/preview?anomaly_mode=post', headers={'X-Admin-User':'manager'})
    assert preview.status_code == 200
    for text in ('사전 탐지','사후 처리','응답 전 중첩','단시간 복수 구매','한도 초과','계정별 구매 사례'):
        assert text in preview.text
    live = client.get('/?anomaly_mode=post', headers={'X-Admin-User':'manager'})
    assert live.status_code == 200
    assert '시안용 샘플 데이터' not in live.text
    assert '조회 실패' in live.text and 'TG-B1' not in live.text
    assert live.headers['cache-control'] == 'no-store'


def test_malformed_health_is_read_error_instead_of_page_crash(monkeypatch):
    import ticket_guard_admin.anomaly.reader as reader
    client = mongomock.MongoClient('mongodb://unused/test', tz_aware=True)
    monkeypatch.setattr(reader, 'MongoClient', lambda *a, **k: client)
    now = datetime.now(timezone.utc)
    client.test.anomaly_public.insert_one(build_preview() | dict(_id='all',scope='all',asOf=now.isoformat(),ruleVersion='version'))
    client.test.anomaly_health.insert_one(dict(_id='ingest',status='ready',asOf='invalid-date',ruleVersion='version'))
    with pytest.raises(AnomalyReadError):
        read_anomaly('mongodb://unused/test', now=now)
