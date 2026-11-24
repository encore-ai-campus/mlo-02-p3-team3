"""
[홍석환] 실제 화면 경로의 격리 브라우저 자료 생성
메모리 공개 문서로 일곱 단계·다양한 연결을 렌더링하며 운영 DB에 쓰지 않는다.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import mongomock
from fastapi.testclient import TestClient

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.settings import AdminSettings
from published_flow_fixture import published_documents


def render_fixture():
    current, series = published_documents(datetime.now(timezone.utc))
    rows = [('shows','공연 목록',4,40),('detail','상세·회차 선택',3,30),('queue','대기열',2,20),
            ('captcha','보안문자',1,15),('select','좌석·수량 선택',2,12),('complete','예매 완료',0,10),('other','기타·미확인',1,0)]
    m = current['result']['metrics']
    m['nodes'] = [dict(id=id_, label=label, order=i, current=count, entered=entered, ongoing=min(count,entered),
                       dwell=dict(samples=max(0,entered-count),meanSeconds=10.5+i*3 if entered else None,
                                  medianSeconds=8.5+i*2 if entered else None))
                  for i,(id_,label,count,entered) in enumerate(rows)]
    edges=[]
    for a,b in zip(rows[:5],rows[1:6]):
        edges.append(dict(source=a[0], target=b[0], kind='forward', moves=b[3], numerator=b[3],
                          denominator=a[3],ratio=b[3]/a[3],ongoing=a[2]))
    for source,target,kind,n,d in [('queue','other','forward',1,20),('select','detail','backward',2,12),
                                  ('captcha','captcha','retry',3,15),('queue','cancelled','cancel',1,20)]:
        edges.append(dict(source=source,target=target,kind=kind,moves=n,numerator=n,denominator=d,ratio=n/d,ongoing=0))
    m.update(current=sum(r[2] for r in rows),edges=edges,booking=dict(numerator=10,denominator=20,ratio=.5))
    series['result']['presence'][-1]['current']=m['current']
    series['result']['booking'][-1].update(numerator=10,denominator=20,ratio=.5,ongoing=4)
    client=mongomock.MongoClient('mongodb://localhost/isolated_flow',tz_aware=True)
    client.isolated_flow.flow_read_current.insert_one(current)
    client.isolated_flow.flow_read_series.insert_one(series)
    with patch('ticket_guard_admin.user_flow.reader.MongoClient',return_value=client), patch('ticket_guard_admin.pages.build_monitoring_preview',side_effect=AssertionError('샘플 호출 금지')):
        app=create_admin_dashboard_app(AdminSettings(mongo_uri='mongodb://unused/isolated_flow',base_path='/admin',_env_file=None))
        response=TestClient(app,headers={'X-Admin-User':'isolated-check'}).get('/')
    assert response.status_code==200 and 'monitor-live-data' in response.text
    assert 'data-flow-state=' not in response.text
    output=Path(__file__).resolve().parents[2]/'.venv/published-fixture.html'
    output.write_text(response.text,encoding='utf-8')
    preview=TestClient(app,headers={'X-Admin-User':'isolated-check'}).get('/preview')
    output.with_name('preview-fixture.html').write_text(preview.text,encoding='utf-8')
    print('격리된 실제 화면 자료 생성 완료: '+str(output))


if __name__=='__main__':
    render_fixture()
