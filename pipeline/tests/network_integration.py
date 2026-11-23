"""Isolated Kafka, real Spark streaming, original storage and MongoDB publication."""
import json
import os
import tempfile
import time
from pathlib import Path

from pymongo import MongoClient
from pyspark.sql import SparkSession

from mongo_writer import write_batch
from network_metrics.metrics import iso
from network_metrics.runtime import NetworkProcessor
from network_metrics.store import publish
from user_flow.verification import VerificationPending

KAFKA = os.environ['TEST_KAFKA']
MONGO = os.environ['TEST_MONGO']
TOPIC = 'network-verification'
SOURCE = 'isolated-network-test'


class Message:
    def __init__(self, row):
        self.row = row

    def topic(self):
        return self.row.topic

    def partition(self):
        return self.row.partition

    def offset(self):
        return self.row.offset

    def value(self):
        return bytes(self.row.value)

    def key(self):
        return None

    def timestamp(self):
        return self.row.timestampType, int(self.row.timestamp.timestamp() * 1000)

    def headers(self):
        return []


def event(identity, at, status, timing, url, request_id=None, kind='server_request'):
    return json.dumps(dict(schemaVersion=2, eventId=identity, eventType=kind, source='server',
                           occurredAt=iso(at), receivedAt=iso(at),
                           details=dict(requestId=request_id or identity, request=dict(url=url),
                                        response=dict(status=status), timing=dict(headersReadyMs=timing))))


def main():
    spark = (SparkSession.builder.appName('ticketguard-network-verification')
             .config('spark.sql.session.timeZone', 'UTC').getOrCreate())
    spark.sparkContext.setLogLevel('ERROR')
    now = int(time.time() // 300) * 300 + 10
    bucket = now - 330
    with MongoClient(MONGO, tz_aware=True) as mongo, tempfile.TemporaryDirectory() as checkpoint:
        raw = mongo['ticketguard_raw']
        target = mongo['ticketguard']

        def kafka(reader):
            return reader.format('kafka').option('kafka.bootstrap.servers', KAFKA).option('subscribe', TOPIC)

        def send(messages):
            spark.createDataFrame([(value,) for value in messages], ['value']).write.format('kafka').option(
                'kafka.bootstrap.servers', KAFKA).option('topic', TOPIC).save()

        def mirror():
            records = kafka(spark.read).option('startingOffsets', 'earliest').load().collect()
            write_batch(raw, [Message(record) for record in records], SOURCE, time.monotonic() + 10)

        def run_batch():
            processor = NetworkProcessor(raw, target, SOURCE, checkpoint, threshold=100, clock=lambda: now)
            processor._verified = lambda records: verified_once(processor, records)
            query = (kafka(spark.readStream).option('startingOffsets', 'earliest').load()
                     .writeStream.foreachBatch(processor.batch)
                     .option('checkpointLocation', str(Path(checkpoint) / 'spark'))
                     .trigger(availableNow=True).start())
            query.awaitTermination()

        def verified_once(processor, records):
            from user_flow.verification import verified_records
            return verified_records(processor.raw, processor.source, records, include_ingested_at=True)

        def visible():
            current = target.network_read_current.find_one({'_id': 'all'})
            series = target.network_read_series.find_one({'_id': 'all'})
            assert current['version'] == series['version']
            return current, series

        send([event('one', bucket, 200, 31, '/'),
              event('duplicate', bucket, 200, 31, '/', request_id='one'),
              event('two', bucket, 404, 100, '/detail/show-1/'),
              event('three', bucket, 503, 110, '/api/action/?fixture=ignored'),
              event('headers', bucket, 503, 110, '/api/action/', request_id='three', kind='server_response_headers')])
        mirror()
        run_batch()
        before, series = visible()
        assert before['result']['data']['summary'] == dict(requests=3, http4xx=1, http5xx=1, measured=3, slow=1)
        assert series['result']['buckets'][-2]['requests'] == 3
        assert series['result']['buckets'][-1]['requests'] is None
        assert 'fixture=ignored' not in str(before)

        send([event('late', bucket - 300, 500, None, '/api/action/')])
        mirror()
        run_batch()
        current, series = visible()
        expected = dict(requests=4, http4xx=1, http5xx=2, measured=3, slow=1)
        assert current['version'] != before['version'] and current['result']['data']['summary'] == expected
        assert series['result']['buckets'][-3]['http5xx'] == 1
        assert sum(row['requests'] for row in current['result']['data']['features']) == 4
        prior = current['version']

        class FailingSeries:
            def find_one(self, *args):
                return None

            def update_one(self, *args, **kwargs):
                raise RuntimeError('isolated partial write')

        class PartialTarget:
            network_versions = target.network_versions
            network_series = FailingSeries()
            network_public = target.network_public

        payload = dict(current['result']['data'], buckets=series['result']['buckets'])
        try:
            publish(PartialTarget(), payload, [dict(topic=TOPIC, partition=0, start=0, end=99)], now)
        except RuntimeError:
            assert visible()[0]['version'] == prior
        else:
            raise AssertionError('Partial write should fail')

        send([event('missing-original', bucket, 500, 150, '/')])
        try:
            run_batch()
        except Exception as exc:
            assert VerificationPending.__name__ in str(exc)
            assert visible()[0]['version'] == prior
        else:
            raise AssertionError('Original mismatch should fail')
        print(f'network_integration_verified requests={expected["requests"]} 5xx={expected["http5xx"]}')
    spark.stop()


if __name__ == '__main__':
    main()
