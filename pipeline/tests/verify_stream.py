"""
[홍석환] 격리 Kafka부터 공개 뷰까지 실제 통합 검사
기존 원본 소비 루프를 실행하고 합성 입력·중복·읽기 권한·한 시간 출력을 확인한다.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

from confluent_kafka import Producer, KafkaException, KafkaError
from confluent_kafka.admin import AdminClient, NewTopic
from pymongo import MongoClient
from pymongo.errors import OperationFailure


def wait_for(function, seconds=120):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = function()
        if value: return value
        time.sleep(.25)
    raise AssertionError('격리 통합 검사 대기 시간 초과')


def main():
    admin = AdminClient({'bootstrap.servers':'kafka:9092'})
    for future in admin.create_topics([NewTopic('isolated.events',num_partitions=2,replication_factor=1)]).values():
        try: future.result()
        except KafkaException as exc:
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS: raise
    process = subprocess.Popen([sys.executable,'/app/main.py'])
    client = MongoClient('mongodb://flow_reader:isolated-flow-reader@metrics:27017/ticketguard_flow?authSource=ticketguard_flow',tz_aware=True)
    raw = MongoClient('mongodb://isolated_reader:isolated-reader-password@raw:27020/ticketguard_raw?authSource=ticketguard_raw')
    try:
        producer = Producer({'bootstrap.servers':'kafka:9092'})
        ends = {}
        def delivered(error, message):
            if error: raise AssertionError('시험 입력 전달 실패')
            ends[message.partition()] = max(ends.get(message.partition(),0), message.offset()+1)
        now = datetime.now(timezone.utc).isoformat()
        events = []
        for i in range(3):
            for j, (kind, stage) in enumerate([('queue_enter','queue'),('captcha_success','select'),('booking_confirm_success','complete')]):
                event = dict(schemaVersion=1,eventId=f'isolated-{i}-{j}',eventType=kind,
                    occurredAt=now,receivedAt=now,source='server',result='success',
                    pageInstanceId=f'tab-{i}',bookingAttemptId=f'attempt-{i}',
                    isSimulation=True,showId='isolated-show',scheduleId='isolated-schedule',details={})
                data=json.dumps(event).encode(); events.append(data)
                producer.produce('isolated.events',key=str(i).encode(),value=data,on_delivery=delivered)
        assert producer.flush(10)==0
        db = client.get_default_database()
        def published():
            current=db.flow_read_current.find_one({'_id':'all'})
            ranges = {r['partition']:r['end'] for r in current['result']['ranges']} if current else {}
            if current and current['result']['metrics']['booking']['denominator']==3 and all(ranges.get(p,0)>=end for p,end in ends.items()):
                return current
        current = wait_for(published)
        m = current['result']['metrics']
        assert m['booking']==dict(numerator=3,denominator=3,ratio=1)
        assert sum(n['current'] for n in m['nodes'])==m['current']
        assert (datetime.fromisoformat(m['asOf'])-datetime.fromisoformat(m['windowStart'])).total_seconds()==3600
        series=wait_for(lambda: db.flow_read_series.find_one({'_id':'all','version':current['version']}))
        assert len(series['result']['presence'])==len(series['result']['booking'])==720
        for data in events: producer.produce('isolated.events',value=data,on_delivery=delivered)
        assert producer.flush(10)==0
        wait_for(lambda: raw.ticketguard_raw.booking_events.count_documents({})>=18)
        current=wait_for(published)
        assert current['result']['metrics']['booking']['numerator']==3
        try:
            db.flow_versions.find_one()
            raise AssertionError('관리자 읽기 계정의 내부 컬렉션 접근 허용')
        except OperationFailure as exc:
            assert exc.code==13
        try:
            db.flow_read_current.insert_one({'forbidden':True})
            raise AssertionError('관리자 쓰기 허용')
        except OperationFailure as exc:
            assert exc.code==13
        print(json.dumps(dict(result='passed',input_records=18,unique_attempts=3,
            booking_successes=3,partitions=2,series_points=720,reader_permissions='verified',
            note='격리 Kafka·원본 적재·실제 Spark·공개 뷰 검사이며 운영 연결이나 화면 검사가 아님'),ensure_ascii=False))
    finally:
        process.terminate()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        client.close(); raw.close()


if __name__=='__main__':
    main()
