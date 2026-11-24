"""Opaque Django session cookies backed exclusively by MongoDB."""
import hashlib
from datetime import timezone as datetime_timezone
from django.contrib.sessions.backends.base import SessionBase, CreateError, UpdateError
from django.utils import timezone
from pymongo.errors import DuplicateKeyError
from .prevention_store import Store


class SessionStore(SessionBase):
    @staticmethod
    def identity(key):
        return hashlib.sha256(key.encode()).hexdigest()

    @property
    def collection(self):
        collection = Store().db.web_sessions
        collection.create_index('expires_at', expireAfterSeconds=0)
        return collection

    def load(self):
        row = self.collection.find_one({'_id': self.identity(self.session_key)}) if self.session_key else None
        if row and row['expires_at'].replace(tzinfo=datetime_timezone.utc) > timezone.now():
            self.revision = row['revision']
            return self.decode(row['data'])
        self._session_key = None
        return {}

    def exists(self, session_key):
        return bool(self.collection.find_one({'_id': self.identity(session_key), 'expires_at': {'$gt': timezone.now()}}, {'_id': 1}))

    def create(self):
        while True:
            self._session_key = self._get_new_session_key()
            try:
                self.save(must_create=True)
            except CreateError:
                continue
            self.modified = True
            return

    def save(self, must_create=False):
        if self.session_key is None:
            return self.create()
        data = self.encode(self._get_session(no_load=must_create))
        revision = getattr(self, 'revision', 0)
        row = dict(_id=self.identity(self.session_key), data=data, expires_at=self.get_expiry_date(), revision=revision+1)
        if must_create:
            try: self.collection.insert_one(row)
            except DuplicateKeyError as exc: raise CreateError from exc
        else:
            result = self.collection.replace_one({'_id': row['_id'], 'revision': revision,
                                                  'expires_at': {'$gt': timezone.now()}}, row)
            if result.matched_count != 1:
                raise UpdateError
        self.revision = revision + 1

    def delete(self, session_key=None):
        key = session_key or self.session_key
        if key:
            self.collection.delete_one({'_id': self.identity(key)})

    @classmethod
    def clear_expired(cls):
        cls().collection.delete_many({'expires_at': {'$lte': timezone.now()}})
