"""격리 Kafka·실제 Spark foreachBatch·MongoDB 적재/재전달 검사. 운영 주소 사용 금지."""
import json
import os
import tempfile
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from uuid import uuid4

from pymongo import MongoClient
from pyspark.sql import SparkSession

from ingestion.mongo_writer import write_batch
from anomaly.rules import Policy
from anomaly.store import refresh


def run():
    if os.environ.get('ANOMALY_ISOLATED_TEST') != '1':
        raise ValueError('Isolated test acknowledgement required')
    kafka = 'anomaly-test-kafka:9092'
    mongo = 'anomaly-test-mongo'
    identity = uuid4().hex[:12]
    topic = 'anomaly-integration-' + identity
    config = SimpleNamespace(mongo_host=mongo, mongo_port=27017, database='anomaly_test_' + identity,
        username=None, password=None, timeout_ms=10000, retries=2, bulk_size=20)
    now = datetime.now(timezone.utc).timestamp()
    def event(identity, kind, at, **extra):
        row = dict(schemaVersion=1, eventId=identity, eventType=kind,
            occurredAt=datetime.fromtimestamp(at, timezone.utc).isoformat(),
            receivedAt=datetime.fromtimestamp(at, timezone.utc).isoformat(),
            source='server', userId='integration-account', bookingAttemptId='attempt',
            showId='show', scheduleId='evening', pageInstanceId='page', result='success',
            isSimulation=False, details={})
        row.update(extra)
        return row
    records = [event('start','queue_enter',now-500), event('request-a','booking_confirm_request',now-450),
        event('request-b','booking_confirm_request',now-448),
        event('purchase','booking_confirm_success',now-440,details=dict(bookingId='B1',quantity=2))]
    values = [json.dumps(r) for r in records] + [json.dumps(records[1]), 'malformed']
    os.environ['ANOMALY_RETRY_SECONDS'] = '5'
    os.environ['ANOMALY_FAST_SECONDS'] = '120'
    spark = SparkSession.builder.appName('ticketguard-isolated-anomaly-test').config('spark.ui.enabled','false').getOrCreate()
    spark.sparkContext.setLogLevel('ERROR')
    client = MongoClient(mongo, tz_aware=True)
    try:
        spark.createDataFrame([(value,) for value in values], ['value']).write.format('kafka').option('kafka.bootstrap.servers',kafka).option('topic',topic).save()
        source = spark.readStream.format('kafka').option('kafka.bootstrap.servers',kafka).option('subscribe',topic).option('startingOffsets','earliest').load()
        checkpoint = tempfile.mkdtemp(prefix='anomaly-integration-')
        query = source.writeStream.foreachBatch(partial(write_batch,config=config)).option('checkpointLocation',checkpoint).trigger(availableNow=True).start()
        if not query.awaitTermination(90):
            query.stop()
            raise RuntimeError('Spark integration timed out')
        db = client[config.database]
        assert db.clean_events.count_documents({}) == 4
        assert db.quarantine_events.count_documents({}) == 1
        assert db.anomaly_events.count_documents({}) == 2
        assert db.clean_events.find_one({'_id':'request-a'})['isAnomaly'] is False
        assert db.clean_events.find_one({'_id':'request-b'})['isAnomaly'] is True
        assert db.clean_events.find_one({'_id':'purchase'})['isAnomaly'] is True
        assert db.anomaly_public.find_one({'_id':'all'})['accounts'][0]['quantity'] == 2
        # 별도 체크포인트에서 같은 Kafka 입력을 재소비해도 저장 건수는 유지된다.
        replay = source.writeStream.foreachBatch(partial(write_batch,config=config)).option('checkpointLocation',tempfile.mkdtemp(prefix='anomaly-replay-')).trigger(availableNow=True).start()
        if not replay.awaitTermination(90):
            replay.stop()
            raise RuntimeError('Spark replay timed out')
        assert db.anomaly_events.count_documents({}) == 2
        assert db.clean_events.count_documents({}) == 4
        print('ANOMALY_INTEGRATION_PASSED real Kafka + Spark streaming + MongoDB + replay', flush=True)
    finally:
        client.close()
        spark.stop()


if __name__ == '__main__':
    run()
