"""범위별 단일 게시물만 읽는다. 저장 중·오류·오래된 게시물은 수치로 제공하지 않는다."""
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.errors import PyMongoError


class AnomalyReadError(ValueError):
    pass


def read_anomaly(uri, scope='all', *, now=None):
    if not uri:
        raise AnomalyReadError('조회 실패 · 관리자 MongoDB 연결 설정 필요')
    now = now or datetime.now(timezone.utc)
    try:
        with MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
                         socketTimeoutMS=3000) as client:
            database = client.get_default_database()
            before = database.anomaly_health.find_one({'_id': 'ingest'}, max_time_ms=3000)
            document = database.anomaly_public.find_one({'_id': scope}, max_time_ms=3000)
            after = database.anomaly_health.find_one({'_id': 'ingest'}, max_time_ms=3000)
            if before is None or document is None:
                return None
            if before != after or before.get('status') != 'ready':
                raise AnomalyReadError('처리 중 또는 처리 실패 · 이전 수치 공개 보류')
            if document.get('contractVersion') != 1 or document.get('scope') != scope:
                raise AnomalyReadError('이상 관측 저장 계약 불일치')
            stamp = datetime.fromisoformat(document['asOf'])
            if stamp.tzinfo is None or not -30 <= (now-stamp).total_seconds() <= 120:
                raise AnomalyReadError('오래된 자료 · Spark 갱신 상태 확인 필요')
            health_at = before.get('asOf')
            if not isinstance(health_at, datetime) or health_at.tzinfo is None:
                raise AnomalyReadError('이상 관측 저장 계약 불일치')
            if before.get('ruleVersion') != document.get('ruleVersion') or int(health_at.timestamp()*1000) != int(stamp.timestamp()*1000):
                raise AnomalyReadError('게시 버전 불일치 · 재조회 필요')
            if not isinstance(document.get('policy'), dict) or not isinstance(document.get('counts'), dict):
                raise AnomalyReadError('이상 관측 저장 계약 불일치')
            for key in ('overlap', 'retry', 'pages', 'fast', 'purchases', 'limit'):
                value = document['counts'].get(key)
                if value is not None and (type(value) is not int or value < 0):
                    raise AnomalyReadError('이상 관측 집계 형식 오류')
            if len(document.get('detections', [])) > 200 or len(document.get('accounts', [])) > 200:
                raise AnomalyReadError('이상 관측 게시 상한 초과')
            return document
    except PyMongoError:
        raise AnomalyReadError('조회 실패 · 이상 관측 읽기 권한과 연결 확인 필요') from None
    except (KeyError, TypeError, OverflowError) as exc:
        raise AnomalyReadError('이상 관측 저장 계약 불일치') from exc
