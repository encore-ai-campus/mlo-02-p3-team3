import hashlib
import json
import math
import re
from collections import defaultdict
from urllib.parse import urlsplit

from user_flow.events import iso, stamp

BUCKET_SECONDS = 1
WINDOW_SECONDS = 10
FEATURES = ('shows', 'detail', 'booking_api', 'other')
COUNTS = ('requests', 'http4xx', 'http5xx', 'measured', 'slow')


def feature(url):
    path = urlsplit(url).path if isinstance(url, str) else ''
    if path in ('/', '/shows/'):
        return 'shows'
    if re.fullmatch(r'/detail/[^/]+/', path):
        return 'detail'
    return 'booking_api' if path == '/api/action/' else 'other'


def project(event):
    if event['eventType'] != 'server_request' or event.get('source') != 'server':
        return None
    details = event['details']
    request_id = details.get('requestId')
    status = details.get('response', {}).get('status')
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 128 or type(status) is not int or not 100 <= status <= 599:
        raise ValueError('Invalid server request identity or status')
    ms = details.get('timing', {}).get('headersReadyMs')
    ms = ms if type(ms) in (int, float) and 0 <= ms <= 86_400_000 and math.isfinite(ms) else None
    observation = {
        'id': event['eventId'], 'time': stamp(event['occurredAt']), 'request_id': request_id,
        'status': status, 'headers_ms': ms, 'feature': feature(details.get('request', {}).get('url')),
    }
    observation['fingerprint'] = hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest()
    return observation


def count_requests(requests, threshold):
    return {
        'requests': len(requests),
        'http4xx': sum(400 <= row['status'] < 500 for row in requests),
        'http5xx': sum(500 <= row['status'] < 600 for row in requests),
        'measured': sum(row['headers_ms'] is not None for row in requests),
        'slow': None if threshold is None else sum(row['headers_ms'] is not None and row['headers_ms'] > threshold for row in requests),
    }


def calculate(observations, now, threshold=None, *, covered=()):
    """covered contains bucket starts whose zero-traffic coverage is independently confirmed."""
    end = int(now // BUCKET_SECONDS) * BUCKET_SECONDS
    start = end - WINDOW_SECONDS
    unique = {}
    for row in sorted(observations, key=lambda row: (row['time'], row['id'])):
        if row['time'] >= end:
            continue
        previous = unique.setdefault(row['request_id'], row)
        if any(previous[key] != row[key] for key in ('status', 'headers_ms', 'feature')):
            raise ValueError('Conflicting observations for the same server request')
    by_bucket = defaultdict(list)
    by_feature = defaultdict(list)
    covered = set(covered)
    for row in unique.values():
        if row['time'] < start:
            continue
        bucket = int(row['time'] // BUCKET_SECONDS) * BUCKET_SECONDS
        by_bucket[bucket].append(row)
        by_feature[row['feature']].append(row)
    buckets = []
    for bucket in range(start, end, BUCKET_SECONDS):
        records = by_bucket[bucket]
        observed = bool(records) or bucket in covered
        counts = count_requests(records, threshold) if observed else dict.fromkeys(COUNTS)
        buckets.append(dict(at=iso(bucket), observed=observed, gateway=None, **counts))
    summary = {}
    for key in COUNTS:
        values = [bucket[key] for bucket in buckets if bucket[key] is not None]
        summary[key] = sum(values) if values else None
    features = []
    if summary['requests'] is not None:
        features = [dict(id=key, **count_requests(by_feature[key], threshold)) for key in FEATURES if by_feature[key]]
    latest = max((row['ingested_at'] for row in observations if row.get('ingested_at') is not None), default=None)
    return {
        'contractVersion': 2, 'windowStart': iso(start), 'windowEnd': iso(end), 'bucketSeconds': BUCKET_SECONDS,
        'latestRequestIngestedAt': iso(latest) if latest is not None else None,
        'thresholdMs': threshold, 'summary': summary, 'features': features, 'buckets': buckets,
        'sources': {'django': 'observed' if any(b['observed'] for b in buckets) else 'unobserved',
                    'gateway': 'unobserved', 'health': 'unobserved'},
    }
