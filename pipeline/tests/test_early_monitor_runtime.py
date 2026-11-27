"""실제 flow 배치 진입점의 공개·저장 실패 후 타이머 차단."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import mongomock
import pytest

from configuration.user_flow import FlowConfig
from user_flow.runtime import Processor
from user_flow.events import iso


class Frame:
    def __init__(self,rows): self.rows=rows
    def toLocalIterator(self,**kwargs): return iter(self.rows)


def test_verified_batch_publishes_receipts_without_changing_flow_presence(tmp_path):
    now=2000000000
    db=mongomock.MongoClient(tz_aware=True)
    config=FlowConfig('unused','events','isolated','unused','unused','isolated',str(tmp_path))
    config.raw_wait_seconds=0
    processor=Processor(config,db.raw,db.metrics,clock=lambda:now)
    records=[]
    for i in range(10):
        event=dict(schemaVersion=1,eventId=str(i),eventType='ticketing_action_received',
            occurredAt=iso(now-10+i*.1),receivedAt=iso(now),source='server',userId='u',
            bookingAttemptId='a',showId='s',scheduleId='t',pageInstanceId='page',
            isSimulation=False,result='success',details=dict(action='confirm',contextKnown=True,
            observationRequestId='req-'+str(i)))
        row=SimpleNamespace(topic='events',partition=0,offset=i,value=json.dumps(event).encode())
        records.append(row)
        db.raw.unclassified_events.insert_one(dict(source='isolated',topic='events',partition=0,offset=i,value=row.value))
    processor.batch(Frame(records),0)
    pointer=db.metrics.early_public.find_one()
    document=db.metrics.early_versions.find_one({'_id':pointer['version']})['data']['scopes']['[false,null,null]']
    assert document['signalCount']==1
    state=processor.state()
    try:
        assert state.observations()==[]
    finally: state.close()
    # 원본 대조는 실시간 경로에서 제외 · 실제 저장 실패만 공개를 막는다.
    with patch('user_flow.runtime.observe_early',side_effect=RuntimeError('write failure')):
        with pytest.raises(RuntimeError): processor.batch(Frame(records),1)
    assert db.metrics.early_health.find_one()['status']=='blocked'
    processor.tick()
    assert db.metrics.early_public.find_one()==pointer
    assert db.metrics.early_health.find_one()['status']=='blocked'
