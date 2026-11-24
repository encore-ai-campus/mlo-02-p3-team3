"""State tests must use a real disposable MongoDB replica set."""
import os
from uuid import uuid4
import pytest
from pymongo import MongoClient
from urllib.parse import urlsplit, urlunsplit

@pytest.fixture(autouse=True)
def isolated_mongo_state(monkeypatch):
    uri = os.environ.get('TEST_MONGO_URI')
    if not uri:
        raise RuntimeError('TEST_MONGO_URI must point to an isolated test replica set')
    parts = urlsplit(uri)
    database = 'ticketguard_pipeline_test_' + uuid4().hex
    monkeypatch.setenv('PIPELINE_STATE_MONGO_URI', urlunsplit((parts.scheme, parts.netloc, '/' + database, parts.query, '')))
    yield
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
        client.drop_database(database)

@pytest.fixture
def mongo_database():
    client = MongoClient(os.environ['PIPELINE_STATE_MONGO_URI'], serverSelectionTimeoutMS=5000)
    yield client.get_default_database()
    client.close()
