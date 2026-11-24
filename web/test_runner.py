"""Each test uses a disposable MongoDB database, never the runtime ledger."""
import os
import unittest
from uuid import uuid4
from django.test import override_settings
from django.test.runner import DiscoverRunner
from pymongo import MongoClient

def test_uri(database):
    from pymongo.uri_parser import parse_uri
    uri = os.environ['TEST_MONGO_URI']
    parsed = parse_uri(uri)
    # Test endpoint deliberately uses a no-auth isolated replica set.
    if parsed['username']:
        raise ValueError('Use an isolated no-auth test replica set')
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(uri)
    return urlunsplit((parts.scheme, parts.netloc, '/' + database, parts.query, ''))

class StateIsolation:
    def startTest(self, test):
        self.state_database = 'ticketguard_test_' + uuid4().hex
        self.state_uri = test_uri(self.state_database)
        self.previous_pipeline_uri = os.environ.get('PIPELINE_STATE_MONGO_URI')
        self.pipeline_database = 'ticketguard_pipeline_test_' + uuid4().hex
        os.environ['PIPELINE_STATE_MONGO_URI'] = test_uri(self.pipeline_database)
        self.state_settings = override_settings(WEB_STATE_MONGO_URI=self.state_uri)
        self.state_settings.enable()
        super().startTest(test)

    def stopTest(self, test):
        try:
            super().stopTest(test)
        finally:
            self.state_settings.disable()
            from .prevention_store import connection
            connection(self.state_uri).close()
            connection.cache_clear()
            with MongoClient(self.state_uri, serverSelectionTimeoutMS=5000) as client:
                client.drop_database(self.state_database)
                client.drop_database(self.pipeline_database)
            if self.previous_pipeline_uri is None:
                os.environ.pop('PIPELINE_STATE_MONGO_URI', None)
            else:
                os.environ['PIPELINE_STATE_MONGO_URI'] = self.previous_pipeline_uri

class IsolatedStateRunner(DiscoverRunner):
    def get_resultclass(self):
        base = super().get_resultclass() or unittest.TextTestResult
        return type('IsolatedStateResult', (StateIsolation, base), {})
