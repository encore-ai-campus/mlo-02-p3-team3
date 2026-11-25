"""Server-owned hourly sales windows, staggered in catalogue order."""
import time
from datetime import datetime
from contextvars import ContextVar
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

WINDOW_SECONDS = 3600
STAGGER_SECONDS = 300
transaction_time = ContextVar('booking_schedule_time', default=None)


def window(show_id, now=None):
    from .services import SHOWS
    configured = settings.BOOKING_OPENS_AT
    if not configured:
        return None  # Explicit legacy mode for existing integrations/tests.
    try:
        anchor = datetime.fromisoformat(configured)
        if anchor.utcoffset() is None:
            raise ValueError('timezone required')
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured('BOOKING_OPENS_AT must be an ISO datetime with timezone') from exc
    first = anchor.timestamp() + list(SHOWS).index(show_id) * STAGGER_SECONDS
    if now is None:
        now = transaction_time.get()
        if now is None: now = time.time()
    opened = first if now < first else first + int((now - first) // WINDOW_SECONDS) * WINDOW_SECONDS
    return dict(cycle=str(int(opened)), opens_at=opened, resets_at=opened + WINDOW_SECONDS,
                is_open=now >= first)
