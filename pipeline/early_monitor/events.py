"""서버 업무 이벤트의 허용 필드 투영. 요청 본문·쿠키·정답은 저장하지 않는다."""
import hashlib
import json

from user_flow.events import stamp

KINDS = {'ticketing_action_received', 'login_success', 'login_fail', 'queue_enter',
         'queue_register', 'queue_admitted', 'queue_cancel', 'connection_timeout',
         'captcha_success', 'captcha_wrong', 'captcha_limit_reached', 'captcha_refresh',
         'booking_confirm_request', 'booking_confirm_success', 'booking_confirm_fail',
         'booking_confirm_rejected', 'booking_confirm_replayed', 'booking_access_rejected',
         'booking_not_open'}
POLICY_KINDS = {'prevention_evaluated', 'prevention_detected', 'prevention_applied',
                'prevention_rejected', 'prevention_released'}
KINDS |= POLICY_KINDS
POLICY_FIELDS = ('ruleId', 'ruleVersion', 'policyMode', 'restrictionId', 'restrictionScope',
                 'observed', 'threshold', 'windowSeconds', 'appliedAt', 'expiresAt', 'releasedAt',
                 'releaseReason', 'selectionError', 'entryCause', 'predecessorAttemptId',
                 'purchasedQuantity', 'requestedQuantity', 'excluded', 'loginSucceeded',
                 'openingState', 'selectionValid', 'evidence')


def identifier(value):
    return value if isinstance(value, str) and 0 < len(value) <= 256 else None


def project(event):
    if event.get('source') != 'server' or event.get('eventType') not in KINDS:
        return None
    details = event['details']
    kind = event['eventType']
    row = dict(id=event['eventId'], time=stamp(event['occurredAt']), kind=kind,
               user=identifier(event.get('userId')), attempt=identifier(event.get('bookingAttemptId')),
               show=identifier(event.get('showId')), schedule=identifier(event.get('scheduleId')),
               simulation=event.get('isSimulation') if type(event.get('isSimulation')) is bool else None,
               result=identifier(event.get('result')), error=identifier(event.get('errorCode')),
               request=identifier(details.get('observationRequestId')),
               action=identifier(details.get('action')), booking=identifier(details.get('bookingId')),
               stage=identifier(details.get('observationStage')),
               sequence=event.get('requestSequence') if type(event.get('requestSequence')) is int else None,
               wrong=details.get('wrongCount') if type(details.get('wrongCount')) is int else None)
    if kind == 'ticketing_action_received' and details.get('contextKnown') is not True:
        row.update(attempt=None, show=None, schedule=None, simulation=None)
    if not all(row[key] for key in ('attempt', 'show', 'schedule')):
        row['simulation'] = None
    if kind in POLICY_KINDS:
        from .server_policies import validate_policy
        metadata = {key: details[key] for key in POLICY_FIELDS if key in details}
        validate_policy(kind, metadata, row)
        row['policy'] = metadata
        # Policy scope is explicitly server-owned; login intentionally has no attempt/show.
        row['simulation'] = event.get('isSimulation') if type(event.get('isSimulation')) is bool else None
    row['fingerprint'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return row


def scope(row):
    return json.dumps([row['simulation'], row['show'], row['schedule']], separators=(',', ':'))
