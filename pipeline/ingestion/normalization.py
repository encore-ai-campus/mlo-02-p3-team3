"""기존 적재 진입점의 누락된 최소 정규화. 새 흐름 모드는 이 경로를 사용하지 않는다."""
from datetime import datetime, timezone
import math
from user_flow.events import decode


BOOKING_EVENTS = {'booking_confirm_request', 'booking_confirm_success',
                  'booking_confirm_fail', 'booking_confirm_replayed'}


def booking_fields(event):
    details = event['details']
    fields = {key: details.get(key) for key in ('bookingId', 'quantity', 'totalAmount', 'seatType', 'seats')}
    for key in ('bookingId', 'seatType'):
        value = fields[key]
        if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 256):
            raise ValueError('Invalid booking identifier')
    quantity = fields['quantity']
    if quantity is not None and (type(quantity) is not int or not 0 < quantity < 2**63):
        raise ValueError('Invalid booking quantity')
    amount = fields['totalAmount']
    if amount is not None and (type(amount) not in (int, float) or
                               not 0 <= amount < 2**63 or not math.isfinite(amount)):
        raise ValueError('Invalid booking amount')
    seats = fields['seats']
    if seats is not None and (not isinstance(seats, list) or
                             any(not isinstance(seat, str) or not 0 < len(seat) <= 256 for seat in seats)):
        raise ValueError('Invalid booking seats')
    return fields


def normalize(raw, transport):
    try:
        event = decode(raw)
        if event['eventType'] == 'capture_chunk':
            return None, {'_id': transport['_id'], 'reason': 'chunk_requires_flow_mode'}
        clean = {key: event.get(key) for key in ('eventId', 'eventType', 'userId', 'bookingAttemptId',
                 'showId', 'scheduleId', 'result', 'errorCode', 'isSimulation', 'source')}
        if event.get('source') == 'server' and event['eventType'] in BOOKING_EVENTS:
            clean.update(booking_fields(event))
        for key in ('occurredAt', 'receivedAt'):
            clean[key] = datetime.fromisoformat(event[key].replace('Z', '+00:00'))
        clean.update(_id=event['eventId'], normalizedAt=datetime.now(timezone.utc))
        return clean, None
    except (ValueError, TypeError, KeyError, UnicodeError):
        return None, {'_id': transport['_id'], 'reason': 'invalid_event'}
