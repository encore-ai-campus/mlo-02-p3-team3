"""불변 공개 버전만 읽는다. QA 리더·원본 DB를 참조하지 않는다."""
import hashlib
import json
import math
import time

from pymongo import MongoClient
from pymongo.errors import PyMongoError


class ObservationReadError(ValueError):
    def __init__(self, message, previous_as_of=None):
        super().__init__(message)
        self.previous_as_of = previous_as_of


def count(value):
    return value is None or type(value) is int and value >= 0


def validate(document):
    try:
        if document['contractVersion'] != 1 or document['policy']['mode'] != 'observe':
            raise ValueError()
        if document['windowEnd']-document['windowStart'] != 3600 or document['evaluationSeconds'] != 7200:
            raise ValueError()
        policy = document['policy']
        if type(policy['enabled']) is not bool or type(policy['count']) is not int or not 2 <= policy['count'] <= 25000 or type(policy['seconds']) not in (int,float) or not math.isfinite(policy['seconds']) or not 0 < policy['seconds'] <= 3600:
            raise ValueError()
        if document['ruleVersion'] != hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()[:20]:
            raise ValueError()
        if len(document['stages']) != 5 or len(document['detections']) > 200 or len(document['actions']) > 200:
            raise ValueError()
        if len(document['series']) > 62 or len(document['durations']) != 4:
            raise ValueError()
        for key in ('signalCount','signalAttempts','actionCount','actionAttempts','actionSubjects','unlinkedActions','unknownContext'):
            if not count(document[key]):
                raise ValueError()
        for stage in document['stages']:
            values = [stage[k] for k in ('requests','events','attempts','accounts')] + list(stage['outcomes'].values())
            if not all(count(v) for v in values):
                raise ValueError()
            if stage['events'] is not None and stage['events'] != sum(v or 0 for v in stage['outcomes'].values()):
                raise ValueError()
        for point in document['series']:
            if type(point['observed']) is not bool or not count(point['detections']) or not count(point['actions']):
                raise ValueError()
        for total, field in (('signalCount','detections'),('actionCount','actions')):
            if document[total] is not None and document[total] != sum(p[field] or 0 for p in document['series']):
                raise ValueError()
        if document['actionCount'] is not None and document['actionCount'] != sum(document['actionCounts'].values()):
            raise ValueError()
        for hit in document['detections']:
            if hit['ruleVersion'] != document['ruleVersion'] or hit['mode'] != 'observe':
                raise ValueError()
        for stats in document['durations']:
            if not count(stats['samples']) or not count(stats['unavailable']):
                raise ValueError()
            for key in ('mean','median','p95'):
                value = stats[key]
                if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value < 0):
                    raise ValueError()
                if (stats['samples'] == 0) != (value is None):
                    raise ValueError()
        metrics=document.get('stageDetections',{})
        if not isinstance(metrics,dict) or set(metrics)-{'login_limit','duplicate_progress','captcha_limit','step_bypass','selection_condition','confirm_excess',
                'login_repeated_failure','not_open_repeat','reentry_repeat','selection_repeat','purchase_limit_attempt'}:
            raise ValueError()
        server_rules={'login_repeated_failure':(None,5),'not_open_repeat':(2,10),
                      'reentry_repeat':(2,10),'selection_repeat':(5,10),'purchase_limit_attempt':(None,4)}
        for metric_key,metric in metrics.items():
            if not all(count(metric.get(key)) for key in ('rejectedCount','actionSubjects')):
                raise ValueError()
            if metric_key in server_rules:
                seconds,threshold=server_rules[metric_key]
                if (metric.get('ruleVersion')!='prevention-2026-11-23-v1' or metric.get('windowSeconds')!=seconds
                        or metric.get('threshold')!=threshold or metric.get('mode')!='temporary_restriction'):
                    raise ValueError()
            if not all(count(metric[key]) for key in ('count','eventCount','actionCount','unknownTargets')) or len(metric['cases'])>200 or type(metric['truncated']) is not bool:
                raise ValueError()
            if metric['count'] is not None and (metric['eventCount'] is None or metric['count']>metric['eventCount']):
                raise ValueError()
            if metric['eventCount'] is not None and metric['eventCount']<len(metric['cases']):
                raise ValueError()
            for case in metric['cases']:
                if type(case['at']) not in (int,float) or not math.isfinite(case['at']) or not document['windowStart']<=case['at']<=document['windowEnd'] or not isinstance(case['evidence'],list):
                    raise ValueError()
                if metric_key in server_rules:
                    if (case.get('ruleVersion')!=metric['ruleVersion'] or case.get('threshold')!=metric['threshold']
                            or type(case.get('observed')) is not int or case['observed']<metric['threshold']
                            or case.get('actionStatus') not in ('active','expired','released','unconfirmed')
                            or not isinstance(case.get('restrictionId'),str)):
                        raise ValueError()
                    if case['actionStatus']!='unconfirmed' and (type(case.get('appliedAt')) not in (int,float)
                            or type(case.get('expiresAt')) not in (int,float) or case['expiresAt']-case['appliedAt']!=300):
                        raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ObservationReadError('사전 관측 공개 계약 불일치') from None
    return document


def read(uri, selected='[false,null,null]', now=None):
    if not uri:
        raise ObservationReadError('사전 관측 조회 설정 없음')
    now = time.time() if now is None else now
    try:
        with MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000,
                         connectTimeoutMS=3000, socketTimeoutMS=3000) as client:
            db = client.get_default_database()
            before = db.early_health.find_one({'_id':'pipeline'}, max_time_ms=3000)
            pointer = db.early_public.find_one({'_id':'pipeline'}, max_time_ms=3000)
            if pointer is None:
                if before and before.get('status') in ('blocked','processing'):
                    raise ObservationReadError('사전 관측 처리 중 또는 실패 · 공개 보류')
                return None
            wrapper = db.early_versions.find_one({'_id':pointer['version']}, max_time_ms=3000)
            after = db.early_health.find_one({'_id':'pipeline'}, max_time_ms=3000)
            pointer_after = db.early_public.find_one({'_id':'pipeline'}, max_time_ms=3000)
            if before != after or pointer != pointer_after or not before or not wrapper:
                raise ObservationReadError('사전 관측 게시 중 · 재조회 필요')
            if before.get('status') != 'ready':
                raise ObservationReadError('사전 관측 갱신 실패 또는 처리 중 · 이전 수치 공개 보류', pointer.get('asOf'))
            version = pointer['version']
            payload = wrapper['data']
            if payload['contractVersion'] != 1 or type(payload['inputCount']) is not int or not 0 <= payload['inputCount'] <= 25000 or not isinstance(payload['ranges'],list) or len(payload['ranges']) > payload['inputCount']:
                raise ObservationReadError('사전 관측 입력 범위 계약 불일치')
            if not (before.get('version') == wrapper['version'] == version == hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()):
                raise ObservationReadError('사전 관측 공개 버전 불일치')
            if before.get('asOf') != payload['asOf'] or pointer['asOf'] != payload['asOf'] or not -30 <= now-payload['asOf'] <= 120:
                raise ObservationReadError('사전 관측 갱신 지연 · 새 결과 확인 필요', pointer.get('asOf'))
            scopes = payload['scopes']
            if len(scopes) > 100 or selected not in scopes:
                raise ObservationReadError('사전 관측 조회 범위 없음')
            document = validate(scopes[selected])
            if document['scope'] != selected or document['ruleVersion'] != payload['ruleVersion'] or document['asOf'] != payload['asOf']:
                raise ObservationReadError('사전 관측 범위·버전 불일치')
            return dict(document=document, scopes=sorted(scopes), version=version)
    except ObservationReadError:
        raise
    except (PyMongoError, KeyError, TypeError, ValueError, OverflowError):
        raise ObservationReadError('사전 관측 조회 실패 · 연결·읽기 권한·저장 계약 확인 필요') from None
