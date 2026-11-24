"""Real MongoDB atomic chunk recovery and metadata regressions."""
import base64
import hashlib
import json
from unittest.mock import patch
import pytest
from user_flow.state import State
from user_flow.events import iso

def test_chunk_completion_rolls_back_and_restarts_without_losing_data():
    raw=json.dumps(dict(schemaVersion=1,eventId='whole',eventType='page_enter',
        occurredAt=iso(100),receivedAt=iso(100),source='server',details={})).encode()
    parts=[raw[:30],raw[30:]]
    def chunk(index):
        return json.dumps(dict(schemaVersion=2,eventId='chunk-'+str(index),eventType='capture_chunk',
            occurredAt=iso(100),receivedAt=iso(100),details=dict(recordId='whole',count=2,index=index,
            totalBytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),encoding='base64-json-utf8',
            data=base64.b64encode(parts[index]).decode()))).encode()
    state=State('atomic-recovery')
    state.begin(1)
    state.feed(chunk(0),1,100,0)
    from pymongo.synchronous.collection import Collection
    original = Collection.update_one
    def injected(collection, *args, **kwargs):
        if collection.name == 'events':
            raise RuntimeError('injected write failure')
        return original(collection, *args, **kwargs)
    with patch.object(Collection,'update_one',injected):
        with pytest.raises(RuntimeError):state.feed(chunk(1),1,100,0)
    assert state.db.chunks.count_documents(state._filter())==1
    assert state.db.assembled.count_documents(state._filter())==0
    state.close()
    state=State('atomic-recovery')
    try:
        state.feed(chunk(1),1,100,0)
        state.finish(1,[dict(topic='events',partition=0,start=0,end=2)])
        assert state.ready() and len(state.observations())==1
        assert state.ranges()==[dict(topic='events',partition=0,start=0,end=2)]
    finally:state.close()

def test_metadata_maximum_insert_only_and_namespace_isolation():
    state=State('metadata');other=State('other')
    try:
        state.set_meta('latest',100,maximum=True)
        state.set_meta('latest',90,maximum=True)
        state.set_meta('coverage',1,insert_only=True)
        state.set_meta('coverage',2,insert_only=True)
        assert state.get_meta('latest')==100
        assert state.get_meta('coverage')==1
        assert other.get_meta('latest') is None
    finally:state.close();other.close()
