import base64
import hashlib
import json

from network_metrics.metrics import calculate, project
from user_flow.events import iso
from user_flow.state import State


def event(identity, status=200, ms=20, kind='server_request', at=4810, request_id=None):
    return dict(eventId=identity, eventType=kind, source='server', occurredAt=iso(at),
                details=dict(requestId=request_id or identity, response=dict(status=status),
                             timing=dict(headersReadyMs=ms), request=dict(url='/api/action/?secret=ignored')))


def test_request_counts_replay_and_observation_boundary():
    records = [event('ok'), event('duplicate', request_id='ok'),
               event('client', 404, 100), event('server', 503, 101),
               event('headers', 503, kind='server_response_headers'),
               event('open-bucket', 500, at=7200), event('old', 500, at=3599)]
    rows = [row for raw in records if (row := project(raw)) is not None]
    result = calculate(rows, 7211, 100, covered=[4500])
    assert result['summary'] == dict(requests=3, http4xx=1, http5xx=1, measured=3, slow=1)
    assert result['features'] == [dict(id='booking_api', **result['summary'])]
    assert result['buckets'][0]['requests'] is None and not result['buckets'][0]['observed']
    assert result['buckets'][3]['requests'] == 0 and result['buckets'][3]['observed']
    assert result['buckets'][4]['requests'] == 3
    assert result['buckets'][-1]['requests'] is None
    assert result['sources']['gateway'] == result['sources']['health'] == 'unobserved'
    assert 'secret' not in str(result)


def test_late_observation_is_added_to_closed_bucket_without_an_implicit_threshold():
    first = project(event('ok', ms=None))
    late = project(event('late', status=500, ms=900))
    before = calculate([first], 7211)
    after = calculate([late, first], 7220)
    assert before['buckets'][4]['requests'] == 1
    assert after['buckets'][4]['requests'] == 2
    assert after['summary'] == dict(requests=2, http4xx=0, http5xx=1, measured=1, slow=None)


def test_chunked_request_uses_the_latest_original_ingestion(tmp_path):
    request = event('chunked', status=503)
    encoded = json.dumps(dict(request, schemaVersion=2, receivedAt=request['occurredAt'])).encode()
    middle = len(encoded) // 2
    chunks = [encoded[:middle], encoded[middle:]]

    def wrap(index):
        return json.dumps(dict(schemaVersion=2, eventId=f'part-{index}', eventType='capture_chunk',
                               occurredAt=iso(4810), receivedAt=iso(4810),
                               details=dict(recordId='chunked', count=2, index=index, totalBytes=len(encoded),
                                            sha256=hashlib.sha256(encoded).hexdigest(), encoding='base64-json-utf8',
                                            data=base64.b64encode(chunks[index]).decode()))).encode()

    path = tmp_path / 'network-state'
    state = State(path, projector=project, skip_expired=True)
    state.begin(1)
    assert state.feed(wrap(1), 1, 4900, 4500, ingested_at=4850) is None
    state.close()
    state = State(path, projector=project, skip_expired=True)
    try:
        result = state.feed(wrap(0), 1, 4900, 4500, ingested_at=4840)
        assert result['request_id'] == 'chunked' and result['ingested_at'] == 4850
        assert len(state.observations()) == 1
    finally:
        state.close()
