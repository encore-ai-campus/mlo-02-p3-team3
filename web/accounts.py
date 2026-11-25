"""웹 전용 MongoDB 계정과 가입 감사 이벤트. QA·운영 지표 DB와 분리한다."""
import logging
import re
from datetime import datetime, timezone

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from pymongo.errors import DuplicateKeyError

from .events import build_event
from .prevention_store import Store

USERNAME = re.compile(r'[a-z0-9][a-z0-9_-]{2,31}', re.ASCII)
logger = logging.getLogger('ticketguard.accounts')


class AccountError(ValueError):
    def __init__(self, message, code, status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def canonical_username(value):
    return value.strip().lower() if isinstance(value, str) else ''


def reserved_username(value):
    reserved = {canonical_username(getattr(settings, key, '')) for key in
                ('DEMO_USERNAME', 'DEMO_SECOND_USERNAME', 'DEMO_ADMIN_USERNAME')}
    return value in reserved or value.startswith('qa-user-')


def _record(session, event):
    session.db.account_events.insert_one({'_id': event['eventId'], **event}, session=session.session)
    session.enqueue(event)


def record_signup_event(kind, user=None, *, error_code=None):
    event = build_event(user, kind, result='failure' if error_code else 'success', errorCode=error_code)
    store = Store()
    store.atomic(lambda session:_record(session,event))


def create_account(username, password):
    username = canonical_username(username)
    if not USERNAME.fullmatch(username):
        raise AccountError('아이디는 영문·숫자로 시작하는 3~32자의 영문·숫자·밑줄·하이픈으로 입력해 주세요.', 'INVALID_USERNAME')
    if reserved_username(username):
        raise AccountError('사용할 수 없는 아이디예요. 다른 아이디를 입력해 주세요.', 'USERNAME_RESERVED', 409)
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise AccountError('비밀번호는 8~128자로 입력해 주세요.', 'INVALID_PASSWORD')
    account = {'_id': username, 'username': username, 'password_hash': make_password(password),
               'role': 'customer', 'created_at': datetime.now(timezone.utc)}
    event = build_event(username, 'signup_success')
    store = Store()
    try:
        def save(session):
            session.db.accounts.insert_one(account, session=session.session)
            _record(session, event)
        store.atomic(save)
    except DuplicateKeyError:
        raise AccountError('이미 사용 중인 아이디예요. 다른 아이디를 입력해 주세요.', 'USERNAME_TAKEN', 409) from None
    return username


def authenticate_account(username, password):
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    username = canonical_username(username)
    if not USERNAME.fullmatch(username) or reserved_username(username):
        return False
    account = Store().db.accounts.find_one({'_id': username}, {'password_hash': 1, 'role': 1})
    encoded = account.get('password_hash', '') if account else ''
    valid = check_password(password, encoded)
    return bool(valid and account.get('role') == 'customer')
