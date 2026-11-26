import math
import os
import threading
import time
from contextlib import ExitStack, closing
from itertools import islice
from pathlib import Path

from pymongo import MongoClient

from user_flow.state import State
from user_flow.verification import VerificationPending, verified_records

from .metrics import BUCKET_SECONDS, WINDOW_SECONDS, calculate, iso, project
from .store import cleanup, indexes, publish


class NetworkProcessor:
    def __init__(self, raw, target, source, checkpoint, threshold=None, grace=300, clock=time.time):
        self.raw, self.target, self.source = raw, target, source
        self.checkpoint = Path(checkpoint)
        self.threshold, self.grace, self.clock = threshold, grace, clock
        self.lock = threading.Lock()
        self.failed = False

    def _states(self, stack):
        return tuple(stack.enter_context(closing(State(
            self.checkpoint / name, projector=project, skip_expired=True)))
            for name in ('network-state', 'network-reference'))

    def _verified(self, records):
        deadline = time.monotonic() + 10
        while True:
            try:
                return verified_records(self.raw, self.source, records, include_ingested_at=True)
            except VerificationPending:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.25)

    def batch(self, frame, batch_id):
        with self.lock, ExitStack() as stack:
            state, reference = self._states(stack)
            try:
                now = self.clock()
                oldest = int(now // BUCKET_SECONDS) * BUCKET_SECONDS - WINDOW_SECONDS - self.grace
                ranges = {}
                for item in (state, reference):
                    item.begin(batch_id)
                rows = frame.toLocalIterator(prefetchPartitions=False)
                while records := list(islice(rows, 100)):
                    for row, (original, ingested_at) in zip(records, self._verified(records), strict=True):
                        if ingested_at is None or ingested_at.tzinfo is None:
                            raise VerificationPending('Missing original ingestion time')
                        for item, value in ((state, bytes(row.value)), (reference, original)):
                            observation = item.feed(value, batch_id, now, oldest, ingested_at=ingested_at.timestamp())
                            if observation is not None:
                                item.set_meta('latestRequestIngestedAt', observation['ingested_at'], maximum=True)
                        key = (row.topic, row.partition)
                        span = ranges.setdefault(key, dict(topic=row.topic, partition=row.partition,
                                                          start=row.offset, end=row.offset + 1))
                        span['start'] = min(span['start'], row.offset)
                        span['end'] = max(span['end'], row.offset + 1)
                for item in (state, reference):
                    item.finish(batch_id, list(ranges.values()))
                self.failed = False
                self._publish(state, reference, now)
            except Exception:
                self.failed = True
                raise

    def tick(self):
        with self.lock, ExitStack() as stack:
            if not self.failed:
                self._publish(*self._states(stack), self.clock())

    def _publish(self, state, reference, now):
        if not state.ready() or not reference.ready():
            return
        ranges = sorted(state.ranges(), key=lambda row: (row['topic'], row['partition']))
        if not ranges:
            return
        if ranges != sorted(reference.ranges(), key=lambda row: (row['topic'], row['partition'])):
            raise VerificationPending('Network input ranges do not match')
        results = []
        for item in (state, reference):
            item.prune(now, WINDOW_SECONDS + BUCKET_SECONDS, self.grace, 0)
            result = calculate(item.observations(), now, self.threshold)
            latest = item.get_meta('latestRequestIngestedAt')
            result['latestRequestIngestedAt'] = iso(latest) if latest is not None else None
            results.append(result)
        if results[0] != results[1]:
            raise VerificationPending('Network aggregates do not match the original')
        version = publish(self.target, results[0], ranges, now)
        cleanup(self.target, now, version)


def run():
    from pyspark.sql import SparkSession

    threshold = float(os.environ['NETWORK_HEADERS_THRESHOLD_MS']) if os.environ.get('NETWORK_HEADERS_THRESHOLD_MS') else None
    if threshold is not None and (not math.isfinite(threshold) or threshold <= 0):
        raise ValueError('NETWORK_HEADERS_THRESHOLD_MS must be positive and finite')
    grace = int(os.environ.get('NETWORK_GRACE_SECONDS', '300'))
    if not 0 <= grace <= 3600:
        raise ValueError('NETWORK_GRACE_SECONDS is out of range')
    raw_uri = os.environ['NETWORK_RAW_MONGO_URI']
    target_uri = os.environ['NETWORK_TARGET_MONGO_URI']
    source = os.environ['RAW_SOURCE_ID']
    if not source:
        raise ValueError('RAW_SOURCE_ID is required')
    checkpoint = '/checkpoints/network-v1'
    options = dict(serverSelectionTimeoutMS=3000, connectTimeoutMS=3000, socketTimeoutMS=3000, tz_aware=True)
    spark = SparkSession.builder.appName('ticketguard-network').config('spark.sql.session.timeZone', 'UTC').getOrCreate()
    stop = threading.Event()
    thread = query = None
    failures = []
    try:
        if not spark.sparkContext.master.startswith('local['):
            raise ValueError('Network state requires local Spark')
        with MongoClient(raw_uri, **options) as raw, MongoClient(target_uri, **options) as target:
            database = target.get_default_database()
            if database.name != 'ticketguard':
                raise ValueError('Network publication must use ticketguard')
            processor = NetworkProcessor(raw.get_default_database(), database, source, checkpoint, threshold, grace)
            indexes(database)
            stream = (spark.readStream.format('kafka')
                      .option('kafka.bootstrap.servers', os.environ.get('SPARK_KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092'))
                      .option('kafka.group.id', 'ticketguard-network-v1')
                      .option('subscribe', os.environ.get('KAFKA_EVENTS_TOPIC', 'ticketguard.events.v1'))
                      .option('startingOffsets', 'earliest').option('failOnDataLoss', 'true')
                      .option('kafka.allow.auto.create.topics', 'false')
                      .option('maxOffsetsPerTrigger', 1000).load())
            query = (stream.writeStream.foreachBatch(processor.batch)
                     .option('checkpointLocation', str(Path(checkpoint) / 'spark'))
                     .trigger(processingTime='1 second').start())

            def tick():
                while not stop.wait(1):
                    try:
                        processor.tick()
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                        return

            thread = threading.Thread(target=tick, daemon=True)
            thread.start()
            print('network metrics started', flush=True)
            try:
                while not query.awaitTermination(1):
                    if failures:
                        raise RuntimeError('Network publication ticker failed: ' + failures[0])
            finally:
                stop.set()
                thread.join()
                query.stop()
    finally:
        spark.stop()
