"""
[홍석환] 로컬 Spark용 사용자 흐름 실시간 공개 실행
실시간 경로는 소비한 레코드로만 계산한다. 원본 DB 대조는 사후 정합성 검토가 담당한다.
범위 밖 시각·형식 오류 이벤트는 공개를 멈추지 않고 건너뛴 건수로 기록한다.
"""
import json
import threading
import time
from pathlib import Path
from pymongo import MongoClient
from user_flow.events import decode, iso, stamp
from user_flow.metrics import calculate
from user_flow.state import State
from user_flow.store import indexes, publish, cleanup_unpublished
from early_monitor.events import project as project_early
from early_monitor.store import (begin as begin_early, block as block_early,
                                 observe as observe_early, refresh as refresh_early)
from early_monitor.metrics import Policy as EarlyPolicy

FUTURE_SECONDS = 30  # 서버 시각보다 앞선 이벤트 허용 범위
SKIP_REASONS = ('late', 'future', 'malformed')


def connection(uri, timeout):
    return MongoClient(uri, serverSelectionTimeoutMS=timeout, connectTimeoutMS=timeout,
                       socketTimeoutMS=timeout, tz_aware=True, appname='ticketguard-flow')


class Processor:
    def __init__(self, config, raw_database, target_database, clock=time.time):
        # raw_database는 호출 호환용 · 실시간 경로는 원본 DB를 조회하지 않는다.
        self.config = config; self.raw = raw_database; self.target = target_database; self.clock = clock
        self.lock = threading.RLock()
        self.path = Path(config.checkpoint) / 'derived-state'
        self.failed = False

    def state(self):
        # 범위 밖 이벤트는 멈추지 않고 건너뜀 · 청크 재조립 후 판정되는 경우 포함
        return State(str(self.path), max_events=self.config.max_events, skip_expired=True, skip_future=True)

    def classify(self, value, now):
        """(이벤트, 건너뛴 사유) · 형식 오류와 범위 밖 시각은 공개 중단 대신 건너뜀"""
        try:
            event = decode(value)
            occurred = stamp(event['occurredAt'])
        except (ValueError, TypeError, UnicodeDecodeError):
            return None, 'malformed'
        if occurred > now + FUTURE_SECONDS:
            return event, 'future'
        if occurred < now - self.config.retention - self.config.grace:
            return event, 'late'
        return event, None

    def batch(self, dataframe, batch_id):
        with self.lock:
            state = self.state()
            now = self.clock(); ranges = {}; skipped = dict.fromkeys(SKIP_REASONS, 0)
            state.begin(batch_id)
            try:
                EarlyPolicy.load()  # 설정 오류는 새 관측 쓰기 전에 거부한다.
                begin_early(self.target)
                # 원본 전체를 모으지 않고 한 레코드씩 순회한다.
                for row in dataframe.toLocalIterator(prefetchPartitions=False):
                    raw = bytes(row.value) if row.value is not None else None
                    key = (row.topic, row.partition)
                    r = ranges.setdefault(key, {'topic': row.topic, 'partition': row.partition, 'start': row.offset, 'end': row.offset + 1})
                    r['start'] = min(r['start'], row.offset); r['end'] = max(r['end'], row.offset + 1)
                    event, reason = self.classify(raw, now)
                    if reason in ('malformed', 'future'):
                        skipped[reason] += 1
                        continue
                    # 사전 관측은 자체 2시간 평가 구간을 사용하므로 1시간 밖 이벤트도 전달한다.
                    observe_early(self.target, project_early(event), dict(source=self.config.source,
                        topic=row.topic, partition=int(row.partition), offset=int(row.offset)))
                    if event['eventType'] != 'ticketing_action_received' and not event['eventType'].startswith('prevention_'):
                        if reason == 'late':
                            skipped['late'] += 1
                            continue
                        # 새 수신 관측으로 기존 접속·체류 지표의 활동 시각을 바꾸지 않는다.
                        state.feed(raw, batch_id, now, now - self.config.retention - self.config.grace, FUTURE_SECONDS)
                state.finish(batch_id, list(ranges.values()))
                state.set_meta('coverageStart', now, insert_only=True)
                if any(skipped.values()):
                    total = state.get_meta('skipped', {})
                    state.set_meta('skipped', {k: total.get(k, 0) + skipped[k] for k in SKIP_REASONS})
                self.failed = False
                self._publish(state)
                if state.ready():
                    refresh_early(self.target, self.clock(), allow_recovery=True)
            except Exception as exc:
                self.failed = True
                block_early(self.target)
                self.target.flow_health.replace_one({'_id': 'pipeline'}, {'_id': 'pipeline', 'status': 'blocked',
                    'checkedAt': iso(self.clock()), 'errorClass': type(exc).__name__,
                    'reason': '처리 실패'}, upsert=True)
                raise RuntimeError('사용자 흐름 처리 실패: 공개 보류, 체크포인트 재시작 필요') from None
            finally:
                state.close()

    def tick(self):
        with self.lock:
            if self.failed:
                return
            state = self.state()
            try:
                self._publish(state)
                if state.ready():
                    refresh_early(self.target, self.clock())
            except Exception as exc:
                self.failed = True
                block_early(self.target)
                self.target.flow_health.replace_one({'_id': 'pipeline'}, {'_id': 'pipeline',
                    'status': 'blocked', 'checkedAt': iso(self.clock()),
                    'errorClass': type(exc).__name__}, upsert=True)
                raise
            finally:
                state.close()

    def _publish(self, state):
        now = self.clock()
        if not state.ready():
            self.target.flow_health.replace_one({'_id': 'pipeline'}, {'_id': 'pipeline', 'status': 'waiting_chunks',
                'checkedAt': iso(now), 'pendingSeconds': state.pending_age(now)}, upsert=True)
            return
        if not state.has_completed():
            return
        state.prune(now, self.config.retention, self.config.grace, self.config.presence)
        rows = state.observations()
        contexts = {r['attempt']: (r['simulation'], r['show'], r['schedule']) for r in rows
                    if r['attempt'] and r['simulation'] is not None}
        for row in rows:
            if row['attempt'] in contexts:
                row['simulation'], row['show'], row['schedule'] = contexts[row['attempt']]
        # 관측된 차원만 제공한다. all 외에 완전 일치 차원을 제공하며 임의 조합은 생성하지 않는다.
        scopes = {'all': rows}
        dimensions = {(r['simulation'], r['show'], r['schedule']) for r in rows}
        for key in state.meta_keys('history:'):
            prior_scope = key[len('history:'):]
            if prior_scope != 'all':
                dimensions.add(tuple(json.loads(prior_scope)))
        for simulation, show, schedule in dimensions:
            key = json.dumps([simulation, show, schedule], separators=(',', ':'))
            scopes[key] = [r for r in rows if (r['simulation'], r['show'], r['schedule']) == (simulation, show, schedule)]
        if len(scopes) > 100:
            raise RuntimeError('차원 수 제한 초과')
        prepared = []
        for scope, values in scopes.items():
            calculated_at = self.clock()
            metrics = calculate(values, now, self.config.presence, self.config.retention)
            metrics['calculatedAt'] = iso(calculated_at)
            metrics['latestReceivedAt'] = iso(max(r['received'] for r in values)) if values else None
            coverage_start = state.get_meta('coverageStart', now)
            metrics['liveCoverageStart'] = iso(coverage_start)
            key = 'history:' + scope
            history = state.get_meta(key, [])
            at = int(now)
            history = [h for h in history if at - 9 <= h['epoch'] and h['epoch'] != at]
            history.append({'epoch': at, 'at': iso(at), 'asOf': iso(now), 'current': metrics['current'], 'observed': True})
            # 중단 구간을 0으로 채우지 않는다. 12시간 격자에서 명시적 미관측값으로 제공한다.
            # [홍석환] 현재 설정은 한 시간이며 중단된 구간은 접속·예매 모두 미관측이다.
            first = at - 9
            grid = range(first, at+1)
            known_presence = {h['epoch']: h for h in history}
            metrics['presenceSeries'] = [known_presence.get(t, {'at': iso(t), 'current': None, 'observed': False}) for t in grid]
            known_booking = {int(stamp(h['at'])): h for h in metrics['bookingSeries']}
            metrics['bookingSeries'] = [known_booking.get(t, {'at': iso(t),
                'numerator': 0 if t in known_presence else None,
                'denominator': 0 if t in known_presence else None, 'ratio': None,
                'ongoing': 0 if t in known_presence else None, 'observed': t in known_presence}) for t in grid]
            metrics['seriesBucketSeconds'] = 1
            metrics['seriesWindowSeconds'] = 10
            metrics['scope'] = scope
            metrics['verifiedAt'] = iso(self.clock())
            # 공개 시각은 기준 시각과 달리 실제 저장 직전의 시각이다.
            prepared.append((scope, metrics, key, history))
        for scope, metrics, key, history in prepared:
            publish(self.target, metrics, state.ranges(), self.clock(), scope, self.config.retention, 30)
            state.set_meta(key, history)
        # verified = 계약·계산 검사 통과 · 원본 DB 대조 결과는 사후 정합성 검토에서 따로 기록
        skipped = state.get_meta('skipped', {})
        self.target.flow_health.replace_one({'_id': 'pipeline'}, {'_id': 'pipeline', 'status': 'verified',
            'checkedAt': iso(self.clock()), 'processingSeconds': self.clock() - now,
            'skipped': {k: skipped.get(k, 0) for k in SKIP_REASONS}}, upsert=True)
        cleanup_unpublished(self.target, self.clock())


def run():
    from configuration.user_flow import FlowConfig
    from pyspark.sql import SparkSession
    config = FlowConfig.load()
    spark = SparkSession.builder.appName('ticketguard-user-flow').config('spark.sql.session.timeZone', 'UTC').getOrCreate()
    if not spark.sparkContext.master.startswith('local['):
        raise ValueError('현재 상태 저장 구현은 단일 로컬 Spark만 지원한다')
    target = connection(config.target_uri, config.timeout_ms)
    processor = Processor(config, None, target.get_default_database())
    indexes(target.get_default_database())
    stop = threading.Event()
    def ticker():
        while not stop.wait(config.interval):
            try:
                processor.tick()
            except Exception as exc:
                processor.failed = True
                try:
                    processor.target.flow_health.replace_one({'_id': 'pipeline'}, {'_id': 'pipeline',
                        'status': 'blocked', 'errorClass': type(exc).__name__, 'checkedAt': iso(time.time())}, upsert=True)
                except Exception:
                    pass
                print('flow tick_failed error_class=' + type(exc).__name__, flush=True)
    thread = threading.Thread(target=ticker, daemon=True); query = None
    try:
        source = (spark.readStream.format('kafka').option('kafka.bootstrap.servers', config.kafka)
                  .option('kafka.group.id', config.group).option('subscribe', config.topic)
                  # Kafka 보존 삭제로 비는 구간은 1시간 지표에 영향 없음 · 원본은 raw DB가 보관
                  .option('startingOffsets', 'earliest').option('failOnDataLoss', 'false')
                  .option('maxOffsetsPerTrigger', config.max_offsets).load())
        query = (source.writeStream.foreachBatch(processor.batch).option('checkpointLocation', str(Path(config.checkpoint) / 'spark'))
                 .trigger(processingTime=f'{config.interval} seconds').start())
        thread.start(); query.awaitTermination()
    finally:
        stop.set()
        if query is not None: query.stop()
        if thread.is_alive(): thread.join(timeout=10)
        target.close(); spark.stop()
