"""
[홍석환] 사용자 흐름 실행 전 읽기 전용 연결 검사
원본 위치 인덱스와 지표 공개 뷰를 확인하며 비밀값과 원본 본문은 출력하지 않는다.
"""
import os
from configuration.user_flow import FlowConfig
from user_flow.runtime import connection
from user_flow.verification import COLLECTIONS


def check():
    config = FlowConfig.load()
    raw = connection(config.raw_uri, config.timeout_ms)
    target = connection(config.target_uri, config.timeout_ms)
    reader = None
    try:
        raw_db = raw.get_default_database()
        target_db = target.get_default_database()
        raw.admin.command('ping'); target.admin.command('ping')
        expected = [('source', 1), ('topic', 1), ('partition', 1), ('offset', 1)]
        for name in COLLECTIONS:
            index = raw_db[name].index_information().get('raw_kafka_position_v1')
            if not index or list(index['key']) != expected:
                raise ValueError('원본 위치 인덱스 확인 필요: ' + name)
        for name in ('flow_read_current', 'flow_read_series'):
            info = list(target_db.list_collections(filter={'name': name}))
            if not info or info[0]['type'] != 'view':
                raise ValueError('공개 뷰 확인 필요: ' + name)
        uri = os.environ.get('TICKET_GUARD_ADMIN_MONGO_URI')
        if uri:
            reader = connection(uri, config.timeout_ms)
            db = reader.get_default_database()
            if db.name != target_db.name:
                raise ValueError('관리자와 Spark의 지표 DB 이름 불일치')
            for name in ('flow_read_current', 'flow_read_series'):
                db[name].find_one({'_id': 'all'}, {'version': 1})
            print('관리자 공개 뷰 읽기 확인 완료 (같은 서버 대상 여부는 별도 확인 필요)')
        else:
            print('관리자 읽기 계정 검사는 미수행: 기존 관리자 URI가 전달되지 않음')
        print('원본 위치 인덱스·지표 공개 뷰 검사 완료. 쓰기와 지표 공개는 수행하지 않았다.')
    finally:
        raw.close(); target.close()
        if reader is not None: reader.close()


if __name__ == '__main__':
    try:
        check()
    except Exception as exc:
        print('실행 전 검사 실패 error_class=' + type(exc).__name__)
        raise SystemExit(1)
