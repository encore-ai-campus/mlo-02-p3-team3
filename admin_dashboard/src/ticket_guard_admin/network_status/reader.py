import math
from datetime import timedelta

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from ticket_guard_admin.user_flow.contracts import FlowError, moment

COUNTS = ('requests', 'http4xx', 'http5xx', 'measured', 'slow')
FEATURES = {'shows': '공연 목록', 'detail': '공연 상세', 'booking_api': '공통 예매 API', 'other': '기타'}


class NetworkReadError(ValueError):
    pass


def check(condition):
    if not condition:
        raise NetworkReadError('Invalid network publication')


def validate(current, series):
    try:
        version = current['version']
        data, history = current['result']['data'], series['result']
        check(isinstance(version, str) and 0 < len(version) <= 128)
        check(current['_id'] == series['_id'] == 'all')
        check(version == series['version'] == current['result']['_id'] == history['_id'])
        check(type(data['contractVersion']) is int and data['contractVersion'] in (1, 2))
        bucket_seconds, window_seconds = (1, 10) if data['contractVersion'] == 2 else (300, 3600)
        check(data['bucketSeconds'] == bucket_seconds)
        check(data['windowStart'] == history['windowStart'] and data['windowEnd'] == history['windowEnd'])
        start, end = moment(data['windowStart']), moment(data['windowEnd'])
        check(end - start == timedelta(seconds=window_seconds) and start.timestamp() % bucket_seconds == 0)
        published = moment(current['result']['publishedAt'])
        latest = data['latestRequestIngestedAt']
        if latest is not None:
            moment(latest)
        threshold = data['thresholdMs']
        check(threshold is None or (type(threshold) in (int, float) and 0 < threshold and math.isfinite(threshold)))
        check(data['sources']['gateway'] == data['sources']['health'] == 'unobserved')

        def counts(row, observed):
            selected = {key: row[key] for key in COUNTS}
            for key, value in selected.items():
                if not observed or (key == 'slow' and threshold is None):
                    check(value is None)
                else:
                    check(type(value) is int and value >= 0)
            if observed:
                check(selected['http4xx'] + selected['http5xx'] <= selected['requests'])
                check(selected['measured'] <= selected['requests'])
                check(selected['slow'] is None or selected['slow'] <= selected['measured'])
            return selected

        buckets = history['buckets']
        check(isinstance(buckets, list) and len(buckets) == window_seconds // bucket_seconds)
        clean_buckets = []
        for index, bucket in enumerate(buckets):
            at, observed = bucket['at'], bucket['observed']
            check(moment(at) == start + timedelta(seconds=bucket_seconds * index) and type(observed) is bool)
            check(bucket['gateway'] is None)
            clean_buckets.append(dict(at=at, observed=observed, gateway=None, **counts(bucket, observed)))
        observed = [bucket for bucket in clean_buckets if bucket['observed']]
        check(data['sources']['django'] == ('observed' if observed else 'unobserved'))
        summary = counts(data['summary'], bool(observed))
        rows = data['features']
        check(isinstance(rows, list) and len(rows) <= len(FEATURES) and (observed or not rows))
        features = []
        for row in rows:
            check(row['id'] in FEATURES and all(row['id'] != item['id'] for item in features))
            features.append(dict(id=row['id'], **counts(row, True)))
        for key, total in summary.items():
            if total is not None:
                check(total == sum(bucket[key] for bucket in observed) == sum(row[key] for row in features))
        return dict(version=version, start=start, end=end, published=published, latest=latest,
                    threshold=threshold, summary=summary, features=features, buckets=clean_buckets, bucket_seconds=bucket_seconds)
    except (KeyError, TypeError, OverflowError, FlowError) as exc:
        raise NetworkReadError('Invalid network publication') from exc


def read_network(uri):
    if not uri:
        raise NetworkReadError('Missing admin MongoDB connection')
    try:
        with MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000,
                         connectTimeoutMS=3000, socketTimeoutMS=3000) as client:
            db = client.get_default_database()
            for _ in range(3):
                current = db.network_read_current.find_one({'_id': 'all'}, max_time_ms=3000)
                if current is None:
                    return None
                series = db.network_read_series.find_one({'_id': 'all'}, max_time_ms=3000)
                if series is not None and current.get('version') == series.get('version'):
                    return validate(current, series)
            raise NetworkReadError('Inconsistent network publication')
    except PyMongoError:
        raise NetworkReadError('Network publication read failed') from None
