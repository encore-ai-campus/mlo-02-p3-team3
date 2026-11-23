"""단계별 규칙 탐지 집계. 일반 요청량을 탐지 건수로 대체하지 않는다."""
from .events import scope

# 기존 서버가 실제로 판정한 사건만 사용한다. 반복 여부의 새 기준은 만들지 않는다.
SERVER_RULES = {
    'login_limit': ({'login_fail'}, {'LOGIN_LOCKED'}, {'login_success','login_fail'}, 'login', False),
    'duplicate_progress': ({'booking_access_rejected','booking_confirm_rejected'},
                           {'DUPLICATE_PAGE','ACTIVE_ATTEMPT'}, {'queue_enter','booking_access_rejected'}, 'start', False),
    'captcha_limit': ({'captcha_limit_reached'}, {'CAPTCHA_LIMIT'},
                      {'captcha_success','captcha_wrong','captcha_limit_reached'}, 'verify', True),
    'step_bypass': ({'booking_access_rejected','booking_confirm_rejected'},
                    {'INVALID_STEP','NOT_VERIFIED'}, {'booking_confirm_request','booking_confirm_rejected','booking_access_rejected'}, 'confirm', False),
    'selection_condition': ({'booking_confirm_fail'}, {'SELECTION_LIMIT'},
                            {'booking_confirm_request','booking_confirm_fail','booking_confirm_success'}, 'confirm', False),
}


def calculate(rows, hits, policy, applied):
    output = {}
    for key, (kinds, reasons, evidence_kinds, action, by_attempt) in SERVER_RULES.items():
        matches = {}
        for row in rows:
            if row['kind'] in kinds and row['error'] in reasons and row['result'] in ('failure','rejected'):
                matches.setdefault((row['request'] or row['id'],row['error']),row)
        values = sorted(matches.values(),key=lambda row:(row['time'],row['id']))
        supported = bool(values) or any(row['kind'] in evidence_kinds or
            row['kind']=='ticketing_action_received' and row['action']==action for row in rows)
        missing = sum(not row['attempt'] or not row['user'] for row in values) if by_attempt else 0
        count = (len({(row['user'],row['attempt'],scope(row)) for row in values if row['attempt'] and row['user']})
                 if by_attempt else len(values)) if supported else None
        if by_attempt and values and missing==len(values):
            count=None
        linked_requests = {(row['request'],row['error']) for row in values if row['request']}
        matching_actions = [item for item in applied if (item['request'],item['reason']) in linked_requests]
        output[key] = dict(count=count,eventCount=len(values) if supported else None,
            actionCount=len(matching_actions) if supported and all(row['request'] for row in values) else None,
            unknownTargets=missing,truncated=len(values)>200,
            cases=[dict(eventId=row['id'],at=row['time'],user=row['user'],attempt=row['attempt'],
                        reason=row['error'],evidence=[row['id']],scope=scope(row)) for row in values[-200:]])
    receipts = [row for row in rows if row['kind']=='ticketing_action_received' and row['action']=='confirm'
                and all(row[field] for field in ('user','attempt','show','schedule','request'))
                and row['simulation'] is not None]
    available = policy.enabled and bool(receipts)
    output['confirm_excess'] = dict(
        count=len({(hit['user'],hit['attempt'],hit['scope']) for hit in hits}) if available else None,
        eventCount=len(hits) if available else None,actionCount=None,unknownTargets=0,truncated=len(hits)>200,
        cases=[dict(eventId=hit['eventId'],at=hit['at'],user=hit['user'],attempt=hit['attempt'],
                    reason='CONFIRM_REQUEST_EXCESS',evidence=hit['evidence'],scope=hit['scope']) for hit in hits[-200:]])
    return output
