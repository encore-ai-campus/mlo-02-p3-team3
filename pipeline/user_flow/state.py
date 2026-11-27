"""Transactional MongoDB recovery state; separate from raw, metrics and QA DBs."""
import hashlib
import json
import os
from pymongo import MongoClient, ReturnDocument
from contextvars import ContextVar
from user_flow.events import chunk_part, decode, project
_counts=ContextVar('pipeline_transaction_counts')


class State:
    def __init__(self, namespace, max_events=250000, max_chunks=4096, *, projector=project, skip_expired=False,
                 skip_future=False):
        uri = os.environ.get('PIPELINE_STATE_MONGO_URI')
        if not uri:
            raise ValueError('PIPELINE_STATE_MONGO_URI is required')
        self.client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=15000)
        self.db = self.client.get_default_database()
        self.namespace = hashlib.sha256(str(namespace).encode()).hexdigest()
        self.max_events, self.max_chunks = max_events, max_chunks
        self.projector, self.skip_expired, self.skip_future = projector, skip_expired, skip_future
        self.db.events.create_index([('namespace', 1), ('time', 1), ('event_id', 1)])
        self.db.chunks.create_index([('namespace', 1), ('record', 1), ('index', 1)], unique=True)
        self.db.batches.create_index([('namespace', 1), ('complete', 1), ('batch', 1)])
        self.db.assembled.create_index([('namespace', 1), ('seen', 1)])
        self.db.guards.update_one({'_id': self.namespace}, {'$setOnInsert': {'revision': 0}}, upsert=True)

    def _key(self, identity):
        return self.namespace + ':' + str(identity)

    def _filter(self, **values):
        return dict(namespace=self.namespace, **values)

    def _transaction(self, callback):
        from pymongo.read_concern import ReadConcern
        from pymongo.write_concern import WriteConcern
        with self.client.start_session() as session:
            def operation(s):
                guard=self.db.guards.find_one_and_update({'_id': self.namespace}, {'$inc': {'revision': 1}}, session=s,return_document=ReturnDocument.AFTER)
                if 'counts' not in guard:
                    # Lazy migration under the same namespace serialization guard.
                    guard['counts']={
                        name:self.db[name].count_documents(self._filter(),session=s)
                        for name in ('events','chunks')}
                    self.db.guards.update_one({'_id':self.namespace},{'$set':{'counts':guard['counts']}},session=s)
                token=_counts.set(dict(guard['counts']))
                try:return callback(s)
                finally:_counts.reset(token)
            return session.with_transaction(operation, read_concern=ReadConcern('snapshot'), write_concern=WriteConcern('majority'))

    def close(self):
        self.client.close()

    def begin(self, batch):
        self.db.batches.update_one({'_id': self._key(batch)}, {'$setOnInsert': self._filter(batch=batch, complete=False, ranges=[])}, upsert=True)

    def feed(self, raw, batch, now, oldest, future_seconds=30, *, ingested_at=None):
        def operation(s):
            e = decode(raw)
            ingestion = ingested_at
            completed_chunk = None
            if e['eventType'] == 'capture_chunk':
                d, data = chunk_part(e)
                record = d['recordId']
                meta = {k: d[k] for k in ('recordId', 'count', 'totalBytes', 'sha256')}
                completed = self.db.assembled.find_one({'_id': self._key(record)}, session=s)
                if completed:
                    if completed['meta'] != meta:
                        raise ValueError('Completed chunk metadata conflict')
                    return None
                prior = list(self.db.chunks.find(self._filter(record=record), session=s))
                if any(row['meta'] != meta or (row['index'] == d['index'] and bytes(row['data']) != data) for row in prior):
                    raise ValueError('Chunk conflict')
                inserted=self.db.chunks.update_one({'_id': self._key(record + ':' + str(d['index']))},
                    {'$setOnInsert': self._filter(record=record, index=d['index'], meta=meta, data=data, seen=now)}, upsert=True, session=s)
                if inserted.upserted_id is not None:self._change_count('chunks',1,s)
                if ingestion is not None:
                    self.db.chunk_ingestion.update_one({'_id': self._key(record)},
                        {'$max': {'latest': ingestion}, '$setOnInsert': self._filter()}, upsert=True, session=s)
                if self._count('chunks',s) > self.max_chunks:
                    raise ValueError('Chunk capacity exceeded')
                parts = list(self.db.chunks.find(self._filter(record=record), session=s).sort('index', 1))
                if len(parts) < d['count']:
                    return None
                joined = b''.join(bytes(row['data']) for row in parts)
                if len(joined) != d['totalBytes'] or hashlib.sha256(joined).hexdigest() != d['sha256']:
                    raise ValueError('Chunk integrity error')
                e = decode(joined)
                if e['eventId'] != record or e['eventType'] == 'capture_chunk':
                    raise ValueError('Chunk identity error')
                completed_chunk = (record, meta)
                received = self.db.chunk_ingestion.find_one({'_id': self._key(record)}, session=s)
                if received:
                    ingestion = received['latest']
            p = self.projector(e)
            if p is not None and not (self.skip_expired and p['time'] < oldest) and not (
                    self.skip_future and p['time'] > now + future_seconds):
                if p['time'] > now + future_seconds:
                    raise ValueError('Future event outside allowed range')
                if p['time'] < oldest:
                    raise ValueError('Event outside retention; recomputation required')
                if ingestion is not None:
                    p['ingested_at'] = ingestion
                previous = self.db.events.find_one({'_id': self._key(p['id'])}, session=s)
                if previous and previous['payload']['fingerprint'] != p['fingerprint']:
                    raise ValueError('Conflicting event identity')
                inserted=self.db.events.update_one({'_id': self._key(p['id'])}, {'$setOnInsert': self._filter(
                    event_id=p['id'], time=p['time'], payload=p, batch=batch)}, upsert=True, session=s)
                if inserted.upserted_id is not None:self._change_count('events',1,s)
                if self._count('events',s) > self.max_events:
                    raise ValueError('Event capacity exceeded')
            else:
                p = None
            if completed_chunk:
                record, meta = completed_chunk
                self.db.assembled.insert_one(dict(_id=self._key(record), **self._filter(meta=meta, seen=now)), session=s)
                removed=self.db.chunks.delete_many(self._filter(record=record), session=s)
                self._change_count('chunks',-removed.deleted_count,s)
                self.db.chunk_ingestion.delete_one({'_id': self._key(record)}, session=s)
            return p
        return self._transaction(operation)

    def _count(self,name,session):
        return _counts.get()[name]

    def _change_count(self,name,delta,session):
        self.db.guards.update_one({'_id':self.namespace},{'$inc':{'counts.'+name:delta}},session=session)
        _counts.get()[name]+=delta

    def reconcile_counts(self):
        """Explicit repair for existing namespaces, serialized with feed/prune."""
        def operation(s):
            counts={name:self.db[name].count_documents(self._filter(),session=s) for name in ('events','chunks')}
            self.db.guards.update_one({'_id':self.namespace},{'$set':{'counts':counts}},session=s)
            return counts
        return self._transaction(operation)

    def finish(self, batch, ranges):
        def operation(s):
            row = self.db.meta.find_one({'_id': self._key('ranges')}, session=s)
            merged = {(r['topic'], r['partition']): r for r in row['value']} if row else {}
            for r in ranges:
                old = merged.setdefault((r['topic'], r['partition']), dict(r))
                old['start'] = min(old['start'], r['start'])
                old['end'] = max(old['end'], r['end'])
            self.db.meta.replace_one({'_id': self._key('ranges')}, dict(_id=self._key('ranges'),
                **self._filter(key='ranges', value=list(merged.values()))), upsert=True, session=s)
            self.db.batches.update_one({'_id': self._key(batch)}, {'$set': {'complete': True, 'ranges': ranges}}, session=s)
        self._transaction(operation)

    def ready(self):
        return not self.db.batches.find_one(self._filter(complete=False)) and not self.db.chunks.find_one(self._filter())

    def has_completed(self):
        return bool(self.db.batches.find_one(self._filter(complete=True)))

    def observations(self):
        return [row['payload'] for row in self.db.events.find(self._filter()).sort([('time', 1), ('event_id', 1)])]

    def ranges(self):
        return self.get_meta('ranges', [])

    def get_meta(self, key, default=None):
        row = self.db.meta.find_one({'_id': self._key(key)})
        return row['value'] if row else default

    def set_meta(self, key, value, *, insert_only=False, maximum=False):
        operator = '$setOnInsert' if insert_only else '$max' if maximum else '$set'
        update = {operator: {'value': value}}
        if insert_only:
            update[operator].update(self._filter(key=key))
        else:
            update['$setOnInsert'] = self._filter(key=key)
        self.db.meta.update_one({'_id': self._key(key)}, update, upsert=True)

    def meta_keys(self, prefix):
        return [row['key'] for row in self.db.meta.find(self._filter()) if row['key'].startswith(prefix)]

    def prune(self, now, retention, grace, presence):
        def operation(s):
            boundary = now - retention - grace
            actors = self.db.events.distinct('payload.actor', self._filter(time={'$gte': now-presence-grace}), session=s)
            actors = [a for a in actors if a is not None]
            removed=self.db.events.delete_many(self._filter(time={'$lt': boundary}, **{'payload.actor': {'$nin': actors}}), session=s)
            self._change_count('events',-removed.deleted_count,s)
            latest = self.db.batches.find_one(self._filter(), sort=[('batch', -1)], session=s)
            if latest:
                self.db.batches.delete_many(self._filter(complete=True, batch={'$lt': latest['batch']-10000}), session=s)
            self.db.assembled.delete_many(self._filter(seen={'$lt': boundary}), session=s)
        self._transaction(operation)

    def pending_age(self, now):
        first = self.db.chunks.find_one(self._filter(), sort=[('seen', 1)])
        return now-first['seen'] if first else 0
