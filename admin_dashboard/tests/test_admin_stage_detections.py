"""단계별 카드 배치·조회 범위·대상 근거 연결 계약."""
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from ticket_guard_admin.app import create_admin_dashboard_app
from ticket_guard_admin.early_monitor.preview import build_preview
from ticket_guard_admin.early_monitor.stage_detections import build
from ticket_guard_admin.anomaly.preview import build_preview as anomaly_preview


def cards(stages):
    return {metric['key']:metric for stage in stages for metric in stage['metrics']}


def test_observed_counts_and_evidence_are_assigned_to_ticketing_stages():
    early=build_preview()
    result=build(early,anomaly_preview(),preview=True)
    assert [stage['key'] for stage in result]==['login','queue','selection','confirm']
    metrics=cards(result)
    assert not any(key.startswith('captcha') for key in metrics)
    assert early['document']['stageDetections']['captcha_limit']['count']==1  # 표시만 제외
    assert metrics['purchase_limit_attempt']['implemented'] is True
    assert metrics['purchase_limit_attempt']['state']=='집계 미반영'
    assert metrics['purchase_limit_attempt']['value'] is None  # 구버전 시안에 새 정책 자료를 합성하지 않음
    assert metrics['overlap']['value']==1
    assert metrics['pages']['cases'][0]['eventId']=='c'
    assert metrics['pages']['cases'][0]['action_status']=='조치 연결 근거 없음'


def test_legacy_scope_mismatch_does_not_mix_counts():
    early=build_preview()
    metrics=cards(build(early,anomaly_preview(),legacy_scope='all'))
    assert metrics['overlap']['value'] is None
    assert metrics['overlap']['state']=='동일 조회 범위 미연결'
    assert metrics['login_limit']['value']==0


class Layout(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[]
        self.cards={'pre':0,'post':0}
        self.regions={}
        self.stages=[]
        self.connectors=0
        self.metric_buttons=[]
        self.common_details=0

    def handle_starttag(self,tag,attributes):
        attributes=dict(attributes)
        panel=next((attrs['data-an-panel'] for _,attrs in reversed(self.stack) if 'data-an-panel' in attrs),None)
        if tag=='article' and 'an-card' in attributes.get('class','').split() and panel:
            self.cards[panel]+=1
        if attributes.get('id') in ('an-chart','an-timeline','an-purchase-timeline','early-title'):
            self.regions[attributes['id']]=(panel,any(parent=='details' for parent,_ in self.stack))
        if 'data-detection-stage' in attributes:
            self.stages.append((attributes['data-detection-stage'],panel))
        if 'sd-connector' in attributes.get('class','').split():
            self.connectors+=1
        if 'data-sd-metric' in attributes:
            self.metric_buttons.append(attributes)
        if attributes.get('id')=='sd-common-detail':
            self.common_details+=1
        if tag not in {'meta','link','input','img','br','hr','source','wbr'}:
            self.stack.append((tag,attributes))

    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                del self.stack[i:]
                break


@pytest.mark.parametrize('path',['/','/preview'])
def test_existing_sections_and_new_stage_cards_coexist_without_collapsing(admin_settings,path):
    response=TestClient(create_admin_dashboard_app(admin_settings)).get(path,headers={'X-Admin-User':'manager'})
    assert response.status_code==200
    layout=Layout()
    layout.feed(response.text)
    assert layout.cards=={'pre':4,'post':4}
    assert layout.regions['an-chart']==('pre',False)
    assert layout.regions['an-timeline']==('pre',False)
    assert layout.regions['early-title']==('pre',False)
    assert layout.regions['an-purchase-timeline']==('post',False)
    assert layout.stages==[(key,'pre') for key in ('login','queue','selection','confirm')]
    assert layout.connectors==3
    assert len(layout.metric_buttons)==13
    assert layout.common_details==1
    assert all(button['type']=='button' and button['aria-controls']=='sd-common-detail' for button in layout.metric_buttons)
    assert all(not button['data-sd-metric'].startswith('captcha') for button in layout.metric_buttons)
    assert 'stage_detections.js?v=3' in response.text
    for title in ('고유 감지 시도','응답 전 중첩','재요청 시도','다중 페이지 계정',
                  '요청·응답 추이','최근 감지 목록','계정 타임라인','단계별 탐지 현황',
                  '검토 대상','빠른 완료','단시간 복수 구매','한도 초과','계정별 구매 사례'):
        assert title in response.text
    assert 'sd-legacy' not in response.text
