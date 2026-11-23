import hashlib
import json
from datetime import datetime, timedelta, timezone

from bson import BSON


def indexes(db):
    for name in ('network_versions', 'network_series'):
        db[name].create_index('expiresAt', expireAfterSeconds=0)
        db[name].create_index('publishedAt')


def publish(db, result, ranges, now):
    version = hashlib.sha256(json.dumps((result, ranges), sort_keys=True).encode()).hexdigest()
    pointer = db.network_public.find_one({'_id': 'all'})
    if pointer and pointer['version'] == version:
        return version
    existing = (db.network_versions.find_one({'_id': version}, {'publishedAt': 1})
                or db.network_series.find_one({'_id': version}, {'publishedAt': 1}))
    published_at = existing['publishedAt'] if existing else datetime.fromtimestamp(now, timezone.utc)
    body = {key: value for key, value in result.items() if key != 'buckets'}
    current = dict(_id=version, data=body, ranges=ranges, publishedAt=published_at)
    series = dict(_id=version, windowStart=result['windowStart'], windowEnd=result['windowEnd'],
                  buckets=result['buckets'], publishedAt=published_at)
    if any(len(BSON.encode(document)) > 8 * 1024 * 1024 for document in (current, series)):
        raise ValueError('Network publication is too large')
    for collection, document in ((db.network_versions, current), (db.network_series, series)):
        collection.update_one({'_id': version}, {'$setOnInsert': document, '$unset': {'expiresAt': ''}}, upsert=True)
    db.network_public.replace_one({'_id': 'all'}, {'_id': 'all', 'version': version}, upsert=True)
    return version


def cleanup(db, now, version):
    at = datetime.fromtimestamp(now, timezone.utc)
    # Keep the visible version and allow in-flight readers time to retry.
    for name in ('network_versions', 'network_series'):
        db[name].update_many({'_id': {'$ne': version}, 'expiresAt': {'$exists': False},
                              'publishedAt': {'$lt': at - timedelta(minutes=5)}},
                             {'$set': {'expiresAt': at + timedelta(minutes=5)}})
