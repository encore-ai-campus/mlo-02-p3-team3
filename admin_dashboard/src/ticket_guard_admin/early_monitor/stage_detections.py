"""단계별 탐지 항목과 공개 집계 연결. 일반 트래픽 건수로 탐지를 추정하지 않는다."""
from .presentation import stamp

CATALOG = (
 ('login','로그인',(
  ('login_repeated_failure','반복 로그인 실패','건','early','계정 누적 인증 실패 5회부터 탐지 · 성공 시 초기화 · 시간창 없음 · 5분 제한'),
  ('login_limit','로그인 제한 도달','건','early','계정 로그인 5분 제한 적용 수 · 제한 중 요청 거절과 구분'),)),
 ('queue','예매 시작·대기열',(
  ('not_open_repeat','오픈 전 반복 요청','건','early','동일 계정·공연·회차 · 직전 2초에 예매 시작 오픈 전 거절 10회 · 오픈 시 해제'),
  ('duplicate_progress','중복 진행 위반','건','early','중복 페이지·기존 예매 진행 충돌로 거절된 요청 수'),
  ('pages','다중 페이지 구매 요청','개 계정','legacy','동일 계정·공연·회차의 서로 다른 페이지에서 구매 요청 관측'),
  ('reentry_repeat','반복 재입장','건','early','동일 계정·공연·회차 · 직전 2초 새 시도 10회 · 연결 만료 복구 제외 · 5분 제한'),)),
 ('selection','좌석·인원 선택',(
  ('step_bypass','단계 우회 요청','건','early','잘못된 단계 접근·보안 확인 전 구매 요청의 서버 거절 수'),
  ('selection_condition','선택 조건 위반','건','early','서버가 좌석·수량 선택 조건 위반으로 거절한 요청 수'),
  ('selection_repeat','반복된 잘못된 선택','건','early','동일 예매 시도 · 직전 5초 수량 범위·중복/유효하지 않은 좌석 오류 10회 · 5분 제한'),)),
 ('confirm','예매 확정',(
  ('overlap','응답 전 중첩','개 예매 시도','legacy','서버 구매 요청 처리 구간이 겹친 고유 예매 시도'),
  ('retry','재요청 시도','개 예매 시도','legacy','기존 업무 수락 요청 간격 규칙 · 확정 요청 과다와 합산하지 않음'),
  ('confirm_excess','확정 요청 과다','개 예매 시도','early','직전 시간창의 수신 횟수 기준을 충족한 고유 계정·예매 시도'),
  ('purchase_limit_attempt','구매 한도 초과 시도','건','early','동일 계정·공연·회차 전체 유효 구매량 + 요청량 > 4매 · 확정 취소·환불 차감 · 5분 제한'),)),
)


def build(early, anomaly, *, legacy_scope='all', preview=False):
    published = early['document'].get('stageDetections',{})
    actions_by_event = {row.get('eventId'):row for row in early['document'].get('actions',[]) if row.get('eventId')}
    stages = []
    legacy_cards = {card['key']:card for card in anomaly['pre_cards']}
    for stage_id,label,definitions in CATALOG:
        metrics=[]
        for key,title,unit,source,description in definitions:
            metric=dict(key=key,label=title,unit=unit,description=description,value=None,
                        state='미구현',cases=[],event_count=None,action_count=None,
                        unknown_targets=0,truncated=False,period=early['period'],
                        implemented=source is not None,stage_key=stage_id,stage_label=label)
            if source=='early':
                data=published.get(key)
                state='조회 실패' if early['error'] else '미수집' if not early['available'] else '집계 미반영'
                if data is not None and early['available'] and not early['error']:
                    metric.update(value=data['count'],event_count=data['eventCount'],
                        action_count=data['actionCount'],unknown_targets=data['unknownTargets'],
                        truncated=data['truncated'],cases=[dict(row,time=stamp(row['at'])) for row in data['cases']])
                    metric['rejected_count']=data.get('rejectedCount')
                    metric['action_subjects']=data.get('actionSubjects')
                    state='관측 범위' if data['count'] is not None else '대상 식별 부족' if data['unknownTargets'] else '판정 근거 부족'
                if key=='confirm_excess' and early['available'] and early['document']['policy']['enabled'] is False:
                    state='정책 미활성'
                metric['state']=state
            elif source=='legacy':
                card=legacy_cards[key]
                if anomaly['error']:
                    metric['state']='조회 실패'
                elif not preview and legacy_scope!=early['selected']:
                    metric['state']='동일 조회 범위 미연결'
                else:
                    metric.update(value=card['value'],state=card['state'],period=anomaly['period'])
                    metric['cases']=[dict(eventId=row['eventId'],time=stamp(row['occurredAt']),
                        user=row.get('user'),attempt=row.get('attempt'),reason=row['label'],
                        evidence=[item.get('eventId',item.get('kind','')) for item in row['evidence']])
                        for row in anomaly['detections'] if row['rule']==key]
                    metric['truncated']=anomaly.get('truncated',False)
            for case in metric['cases']:
                applied=actions_by_event.get(case['eventId']) if source=='early' else None
                case['action_status']=(f"적용 · {applied['label']}" if applied else
                    '해당 규칙의 자동 조치 없음' if key=='confirm_excess' else '조치 연결 근거 없음')
                if 'actionStatus' in case:
                    status={'active':'5분 제한 적용', 'expired':'제한 만료', 'released':'제한 해제',
                            'unconfirmed':'제한 적용 근거 미확인'}[case['actionStatus']]
                    case['action_status']=status + (f" · 종료 {stamp(case['expiresAt'])}" if case['expiresAt'] is not None else '')
                    if case.get('releasedAt') is not None:
                        case['action_status'] += f" · 해제 {stamp(case['releasedAt'])} ({case['releaseReason']})"
                    case['reason'] += f" · 관측 {case['observed']} / 기준 {case['threshold']} · {case['ruleVersion']}"
            metrics.append(metric)
        stages.append(dict(key=stage_id,label=label,metrics=metrics))
    return stages
