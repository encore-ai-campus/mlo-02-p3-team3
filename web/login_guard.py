"""Shared MongoDB browser login protection; timestamps are UTC epoch seconds."""
import hashlib
import time
from datetime import datetime, timezone
from functools import wraps
from uuid import UUID, uuid4
from django.conf import settings
from .prevention_store import Store

COOKIE = 'tg_login'
COOKIE_SALT = 'login-guard'
COOKIE_MAX_AGE = 30 * 24 * 3600


class LoginGuard:
    @staticmethod
    def identity(client):
        return hashlib.sha256(client.encode()).hexdigest()

    def reset(self):
        with Store().transaction() as session:
            session.db.browser_guards.delete_many({}, session=session.session)

    @property
    def entries(self):
        with Store().transaction() as session:
            return {row['_id']: row for row in session.db.browser_guards.find(session=session.session)}

    def is_locked(self, client):
        with Store().transaction(guard='browser:'+self.identity(client)) as session:
            key = self.identity(client)
            row = session.db.browser_guards.find_one({'_id': key}, session=session.session)
            if not row or not row['locked_until']:
                return False
            if row['locked_until'] > time.time():
                return True
            session.db.browser_guards.delete_one({'_id': key}, session=session.session)
            return False

    def fail(self, client):
        with Store().transaction(guard='browser:'+self.identity(client)) as session:
            now = time.time()
            key = self.identity(client)
            row = session.db.browser_guards.find_one({'_id': key}, session=session.session)
            if row and row['locked_until'] > now:
                return row['failures'], True
            if row is None or now - row['updated'] > settings.LOGIN_LOCK_SECONDS:
                row = dict(_id=key, failures=0, locked_until=0)
            row['failures'] += 1
            row['updated'] = now
            if row['failures'] >= settings.LOGIN_MAX_FAILURES:
                row['locked_until'] = now + settings.LOGIN_LOCK_SECONDS
            row['expires_at'] = datetime.fromtimestamp(max(now + settings.LOGIN_LOCK_SECONDS, row['locked_until']), timezone.utc)
            session.db.browser_guards.replace_one({'_id': key}, row, upsert=True, session=session.session)
            return row['failures'], bool(row['locked_until'] > now)

    def succeed(self, client):
        with Store().transaction(guard='browser:'+self.identity(client)) as session:
            session.db.browser_guards.delete_one({'_id': self.identity(client)}, session=session.session)


login_guard = LoginGuard()


def login_client(request):
    """This browser's id from the signed cookie; a new id is issued if missing."""
    value = request.get_signed_cookie(COOKIE, default=None, salt=COOKIE_SALT)
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        request.login_client_new = str(uuid4())
        return request.login_client_new


def issue_login_cookie(view):
    """Set the browser id cookie on whatever response the view returns."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        client = getattr(request, 'login_client_new', None)
        if client:
            response.set_signed_cookie(COOKIE, client, salt=COOKIE_SALT, max_age=COOKIE_MAX_AGE,
                                       httponly=True, samesite='Lax', secure=not settings.DEBUG)
        return response
    return wrapped
