"""
[홍석환] 실제 사용자 흐름 공개 지표 페이지
조회·표시 변환을 연결하고 공개 결과 없음과 오류를 샘플 없이 렌더링한다.
"""
import re
from concurrent.futures import ThreadPoolExecutor

from fastapi.responses import HTMLResponse

from ticket_guard_admin.network_status.presentation import for_display as network_for_display
from ticket_guard_admin.network_status.reader import NetworkReadError, read_network
from ticket_guard_admin.pages import templates
from .contracts import FlowError, scope_key
from .presentation import for_display, scope_label, unpublished_layout
from .reader import PublishedFlowReader
from ticket_guard_admin.anomaly.reader import AnomalyReadError, read_anomaly
from ticket_guard_admin.anomaly.presentation import display as anomaly_display
from ticket_guard_admin.early_monitor.reader import read as read_early, ObservationReadError
from ticket_guard_admin.early_monitor.presentation import display as display_early
from ticket_guard_admin.early_monitor.stage_detections import build as build_stage_detections
from ticket_guard_admin.qa_run.reader import QaRunReadError, read_qa_run

# QA 04 · B1 · B4 'DB 원인 분석' 링크 · run_id 시작 시각 + run_to 종료 시각 = Run 구간 조회
QA_RUN_PARAM = 'run_id'
QA_RUN_END_PARAM = 'run_to'
QA_RUN_ID_PATTERN = re.compile(r'run-\d{8}-\d{6}-[0-9a-f]{4}')  # QA create_run_id 형식
DEFAULT_EARLY_SCOPE = '[false,null,null]'
QA_EARLY_SCOPE = '[true,null,null]'  # QA 테스트 계정 트래픽 = isSimulation True
SUPPORTED_PARAMS = {'scope', 'anomaly_mode', 'early_scope', QA_RUN_PARAM, QA_RUN_END_PARAM}


def read_qa_run_id(query_params) -> str | None:
    values = query_params.getlist(QA_RUN_PARAM)
    return values[0] if len(values) == 1 and QA_RUN_ID_PATTERN.fullmatch(values[0]) else None


def render_published_dashboard(request, mongo_uri, *, fragment=False) -> HTMLResponse:
    # Each reader has its own bounded MongoClient. Independent sections must not
    # accumulate their connection timeouts behind the browser's refresh deadline.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='admin-metrics') as reads:
        return _render(request, mongo_uri, fragment=fragment, reads=reads)


def _render(request, mongo_uri, *, fragment, reads):
    context = dict(base_path=request.app.state.base_path,
                   admin_user=getattr(request.state, 'admin_user', ''), is_preview=False,
                   flow=None, flow_error=None, scope='all', scope_choices=[{'value': 'all', 'label': '전체 공개 지표'}],
                   header_status='공개 지표 확인', header_error=False, header_tone='neutral',
                   qa_run_id=read_qa_run_id(request.query_params), qa_run_to=request.query_params.get(QA_RUN_END_PARAM, ''))
    filter_error = None
    try:
        if set(request.query_params) - SUPPORTED_PARAMS or len(request.query_params.getlist('scope')) > 1:
            raise FlowError('unsupported_filter', '지원하지 않는 조회 조건 · 기존 기간·공연 단독 필터 대신 공개된 범위 조합을 선택해 주세요.')
        context['scope'] = scope_key(request.query_params.get('scope', 'all'))
    except FlowError as exc:
        filter_error = exc
    selected_scope = context['scope']
    network_read = reads.submit(read_network, mongo_uri)
    anomaly_read = reads.submit(read_anomaly, mongo_uri, selected_scope)
    default_early_scope = QA_EARLY_SCOPE if context['qa_run_id'] else DEFAULT_EARLY_SCOPE
    early_scope = request.query_params.get('early_scope', selected_scope if selected_scope != 'all' else default_early_scope)
    early_read = (reads.submit(read_early, mongo_uri, early_scope)
                  if len(request.query_params.getlist('early_scope')) <= 1 else None)
    flow_read = reads.submit(PublishedFlowReader(mongo_uri).read, selected_scope) if filter_error is None else None
    qa_run_read = (reads.submit(read_qa_run, mongo_uri, context['qa_run_id'], request.query_params.get(QA_RUN_END_PARAM))
                   if context['qa_run_id'] and not fragment else None)  # 1초 갱신 조각 제외 · 끝난 Run 구간
    try:
        context['network'] = network_for_display(network_read.result())
    except (NetworkReadError, ValueError):
        context['network'] = network_for_display(None)
        context['network_error'] = True
    try:
        if filter_error is not None:
            raise filter_error
        result = flow_read.result()
        context['flow'] = for_display(result)
        context['scope_choices'] = [dict(value=value, label=scope_label(value)) for value in result['scopes']]
        context['header_status'] = '공개 지표 조회 완료'
        context['header_tone'] = 'normal'
    except FlowError as exc:
        context['flow_error'] = dict(code=exc.code, message=exc.message)
        context['flow'] = unpublished_layout()
        context['header_status'] = {'no_publication': '공개 결과 없음', 'scope_not_found': '조회 범위 없음',
                                    'unsupported_filter': '조회 조건 확인'}.get(exc.code, '공개 지표 조회 실패')
        context['header_error'] = exc.code not in ('no_publication', 'scope_not_found', 'unsupported_filter')
    try:
        context['anomaly'] = anomaly_display(anomaly_read.result())
    except (AnomalyReadError, ValueError) as exc:
        context['anomaly'] = anomaly_display(error=str(exc))
    try:
        if len(request.query_params.getlist('early_scope')) > 1:
            raise ObservationReadError('사전 관측 조회 조건 중복')
        context['early'] = display_early(early_read.result(), selected=early_scope)
    except ObservationReadError as exc:
        context['early'] = display_early(error=str(exc), selected=early_scope, previous_as_of=exc.previous_as_of)
    if qa_run_read is not None:
        try:
            context['qa_run'] = qa_run_read.result()
        except QaRunReadError as exc:
            context['qa_run_error'] = str(exc)
    context['detection_stages'] = build_stage_detections(context['early'],context['anomaly'],legacy_scope=context['scope'])
    context['dashboard_data'] = {'flow': context['flow'], 'anomaly': context['anomaly'], 'network': context['network']}
    response = templates.TemplateResponse(request, 'monitor_metrics.html' if fragment else 'monitor_live.html', context)
    response.headers['Cache-Control'] = 'no-store'
    return response
