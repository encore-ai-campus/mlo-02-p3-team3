"""Real replica-set regressions for process concurrency and database outages."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from pymongo import MongoClient
from pymongo.errors import AutoReconnect
from pymongo.synchronous.client_session import ClientSession
from .prevention import login_attempt
from .prevention_store import Store
from .services import FlowError


@override_settings(EVENT_PUBLISHER='mock', CAPTURE_ENABLED=False)
class MongoStorageTests(SimpleTestCase):
    def test_different_processes_share_atomic_purchase_limit(self):
        program = """import os, django
os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
django.setup()
from web.prevention_store import Store
try:
    with Store().transaction() as session:
        session.purchase('shared-scope',os.environ['ATTEMPT_ID'],dict(id=os.environ['ATTEMPT_ID'],quantity=3))
    print('purchased')
except ValueError:
    print('limited')
"""
        def worker(identity):
            env = dict(os.environ, WEB_STATE_MONGO_URI=settings.WEB_STATE_MONGO_URI, ATTEMPT_ID=identity)
            result = subprocess.run([sys.executable,'-c',program], env=env, capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout.strip()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(worker,['one','two'])),['limited','purchased'])
        with Store().transaction() as session:
            self.assertEqual(session.purchased('shared-scope'),3)

    def test_database_outage_returns_503_without_fallback(self):
        client=MongoClient('mongodb://127.0.0.1:1/unavailable',serverSelectionTimeoutMS=100,connectTimeoutMS=100)
        self.addCleanup(client.close)
        with patch('web.prevention_store.connection',return_value=client):
            with self.assertRaises(FlowError) as error:
                login_attempt('outage-user',lambda:True)
        self.assertEqual(error.exception.code,'POLICY_UNAVAILABLE')
        self.assertEqual(error.exception.status,503)

    def test_unknown_commit_result_retries_commit_without_replaying_body(self):
        original=ClientSession.commit_transaction
        calls=[]
        def commit(session):
            original(session)
            calls.append(1)
            if len(calls)==1:
                error=AutoReconnect('injected lost acknowledgement')
                error._add_error_label('UnknownTransactionCommitResult')
                raise error
        body=[]
        with patch.object(ClientSession,'commit_transaction',commit):
            with Store().transaction() as session:
                body.append(1)
                session.purchase('scope','attempt',dict(id='booking',quantity=1))
        self.assertEqual(len(calls),2)
        self.assertEqual(len(body),1)
        with Store().transaction() as session:
            self.assertEqual(session.purchased('scope'),1)
