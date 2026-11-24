"""Source-reviewed MongoDB document shapes; artifact generator only."""
from pathlib import Path
import ast, html, json, textwrap

ROOT = Path(__file__).resolve().parents[1]
PANELS = [[], [], [], []]
def fields(s): return s.split()
def card(db, title, body, kind='collection', source=''):
    if isinstance(body, str): body = body.strip('\n').splitlines()
    PANELS[db].append(dict(title=title, lines=body, kind=kind, source=source))
def detail(db, title, body, source=''):
    card(db, title, body, 'detail', source)
def dataclass_fields(name):
    tree=ast.parse((ROOT/'qa/src/ticket_guard_qa/domain/models.py').read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
    return [n.target.id for n in node.body if isinstance(n,ast.AnnAssign)]

raw=fields('_id source topic partition offset key value headers[]')+['  key','  value']+fields('timestampMs timestampType ingestedAt routing')+['  version','  reason*']
for name in fields('behavior_events booking_events auth_events http_events performance_events browser_errors collector_events raw_chunks unclassified_events'):
    card(0,name,raw,source='raw_consumer/records.py · mongo_writer.py')
card(0,'raw_events',['초기화용','저장 필드 정의 미확인'],'inactive','raw_consumer/mongo/init.js')

card(1,'clean_events',fields('_id eventId eventType userId bookingAttemptId showId scheduleId result errorCode isSimulation source occurredAt receivedAt normalizedAt isAnomaly evaluationStatus bookingId* quantity* totalAmount* seatType* seats[]* evaluatedRules[]* ruleVersion*'),source='ingestion/normalization.py · anomaly/rules.py')
card(1,'quarantine_events',fields('_id reason'),source='ingestion/normalization.py')
card(1,'event_metrics',['[summary]']+fields('_id metricType totalCleanEvents totalQuarantineEvents refreshedAt')+['[event_type]']+fields('_id metricType eventType total success failure rejected lastOccurredAt'),source='ingestion/mongo_writer.py')
anomaly_obs=fields('_id id time received kind user attempt page show schedule simulation result booking quantity request start duration status sequence error fingerprint expiresAt')
card(1,'anomaly_observations',anomaly_obs,source='anomaly/rules.py:project · store.py')
anomaly_hit=fields('_id eventId isAnomaly rule label phase occurredAt user attempt show schedule simulation ruleVersion threshold observed evidence[]')+['  eventId','  at','  kind','evaluationStatus*']
card(1,'anomaly_events',anomaly_hit,source='anomaly/rules.py:hit · store.py')
card(1,'anomaly_public',fields('_id contractVersion scope asOf windowStart windowEnd ruleVersion')+['policy → [S1]','status','counts → [S1]']+fields('attempts subjects evaluated pending unavailable')+['detections[] → anomaly_events','  evaluationStatus は含まない'.replace(' は含まない',' 제외'),'timelines','  {eventId}[]','    at','    kind','    eventId','accounts[] → [S2]','series[]','  at','  overlap','  retry','  observed','truncated','coverage'],source='anomaly/store.py:publication')
card(1,'anomaly_health',fields('_id status asOf* ruleVersion*'),source='anomaly/store.py · ingestion/mongo_writer.py')
count_fields=fields('requests http4xx http5xx measured slow')
card(1,'network_versions',['_id','data → [S3]','ranges[]','  topic','  partition','  start','  end','publishedAt','expiresAt*'],source='network_metrics/store.py · runtime.py')
card(1,'network_series',['_id','windowStart','windowEnd','buckets[]','  at','  observed','  gateway']+['  '+f for f in count_fields]+['publishedAt','expiresAt*'],source='network_metrics/store.py · metrics.py')
card(1,'network_public',fields('_id version'),source='network_metrics/store.py')
card(1,'raw_events',['초기화용','저장 필드 정의 미확인'],'inactive','mongo/init.js')
card(1,'network_read_current',['원본: network_public + network_versions','_id','version','result','  _id','  data → [S3]','  publishedAt'],'view','database/network_views.js')
card(1,'network_read_series',['원본: network_public + network_series','_id','version','result','  _id','  windowStart','  windowEnd','  buckets[] → network_series.buckets'],'view','database/network_views.js')
detail(1,'[S1] anomaly_public.policy / counts',fields('overlap retry pages fast purchases limit'),'anomaly/rules.py:Policy · store.py')
detail(1,'[S2] anomaly_public.accounts[]',fields('user count quantity signals[] bookings[]')+['  id','  quantity','  show','  schedule','  at','evidence[]','  at','  kind','  eventId','durations[]'],'anomaly/store.py:publication')
detail(1,'[S3] network_versions.data',fields('contractVersion windowStart windowEnd bucketSeconds latestRequestIngestedAt thresholdMs summary')+['  '+f for f in count_fields]+['features[]','  id']+['  '+f for f in count_fields]+['sources','  django','  gateway','  health'],'network_metrics/metrics.py:calculate')

card(2,'flow_versions',['_id','metrics → [F1]','ranges[]','  topic','  partition','  start','  end']+fields('scope status verifiedAt publishedAt expiresAt*'),source='user_flow/store.py · runtime.py')
card(2,'flow_series',fields('_id scope version at publishedAt')+['presence[]','  epoch*','  at','  asOf*','  current','  observed','booking[]','  at','  numerator','  denominator','  ratio','  ongoing','  observed','expiresAt*'],source='user_flow/store.py · runtime.py')
card(2,'flow_public',fields('_id version asOf status seriesCutoff'),source='user_flow/store.py')
card(2,'flow_health',fields('_id status checkedAt processingSeconds* pendingSeconds* errorClass* reason*'),source='user_flow/runtime.py')
early_obs=fields('_id id time kind user attempt show schedule simulation result error request action booking stage sequence wrong fingerprint')+['policy* → [F4]','origin','  source','  topic','  partition','  offset','expiresAt']
card(2,'early_observations',early_obs,source='early_monitor/events.py · store.py')
early_hit=fields('id eventId at user attempt scope observed threshold seconds ruleVersion mode evidence[]')
card(2,'early_detections',['[관측 기반]','_id']+early_hit+['status','[서버 판정]','_id']+fields('id time kind user attempt show schedule simulation result error request action booking stage sequence wrong fingerprint')+['policy → [F4]','status'],source='early_monitor/metrics.py · store.py')
action=fields('id request eventId at user attempt scope reason label ruleVersion threshold observed expiresAt releaseStatus status appliedAt* restrictionId* releasedAt* releaseEventId*')
card(2,'early_actions',['_id']+action,source='early_monitor/metrics.py:actions · store.py')
card(2,'early_versions',fields('_id version data')+['  contractVersion','  asOf','  ruleVersion','  scopes','    {scope} → [F5]','  inputCount','  ranges[]','publishedAt','expiresAt*'],source='early_monitor/store.py')
card(2,'early_public',fields('_id version asOf'),source='early_monitor/store.py')
card(2,'early_health',fields('_id status version* asOf*'),source='early_monitor/store.py')
card(2,'flow_read_current',['원본: flow_public + flow_versions']+fields('_id version asOf status seriesCutoff')+['result → flow_versions'],'view','database/flow_permissions.js')
card(2,'flow_read_series',['원본: flow_public + flow_series']+fields('_id version asOf status seriesCutoff')+['result → flow_series'],'view','database/flow_permissions.js')
detail(2,'[F1] flow_versions.metrics',fields('contractVersion calculationVersion asOf windowStart countingUnit presenceTimeoutSeconds current cancelled interrupted')+['booking','  numerator','  denominator','  ratio','nodes[] → [F2]','edges[] → [F3]']+fields('calculatedAt latestReceivedAt liveCoverageStart scope verifiedAt'),'user_flow/metrics.py · runtime.py')
detail(2,'[F2] metrics.nodes[]',fields('id label order current dwell')+['  meanSeconds','  medianSeconds','  samples','ongoing','entered'],'user_flow/metrics.py')
detail(2,'[F3] metrics.edges[]',fields('source target kind moves numerator denominator ratio ongoing'),'user_flow/metrics.py')
policy_fields=fields('ruleId ruleVersion policyMode restrictionId restrictionScope observed threshold windowSeconds appliedAt expiresAt releasedAt releaseReason selectionError entryCause predecessorAttemptId purchasedQuantity requestedQuantity excluded loginSucceeded openingState selectionValid evidence[]')
detail(2,'[F4] early_*.policy', [f+'*' for f in policy_fields],'early_monitor/events.py:POLICY_FIELDS')
detail(2,'[F5] data.scopes.{scope}',fields('contractVersion scope asOf windowStart windowEnd evaluationSeconds policy')+['  enabled','  seconds','  count','  mode','  rule','ruleVersion','status','stages[] → [F6]','stageDetections → [F7]']+fields('signalCount signalAttempts')+['detections[] → [F8]','contexts','  {eventId}[]','    eventId','    kind','    result','    error']+fields('actionCount actionAttempts actionSubjects actionCounts')+['  {동적 label}','actions[] → early_actions 본문','  _id 제외','durations[] → [F9]','series[]','  at','  observed','  detections','  actions']+fields('truncated unlinkedActions unknownContext unknownQueueStage coverage fastPolicy'),'early_monitor/metrics.py:calculate')
detail(2,'[F6] scopes.{scope}.stages[]',fields('key label requests events attempts accounts outcomes')+['  '+s+'*' for s in fields('login_success login_failed login_locked queue_enter booking_not_open queue_register queue_admitted queue_cancel connection_timeout captcha_success captcha_wrong captcha_limit_reached captcha_refresh booking_confirm_request booking_confirm_success booking_confirm_fail booking_confirm_rejected booking_confirm_replayed booking_access_rejected')],'early_monitor/metrics.py:calculate')
detail(2,'[F7] scopes.{scope}.stageDetections', ['{동적 규칙 키}']+['  '+s for s in fields('count eventCount actionCount unknownTargets truncated')]+['  cases[] → [F10]']+['  '+s+'*' for s in fields('rejectedCount actionSubjects pendingEvidence ruleVersion threshold windowSeconds mode')],'stage_detections.py · server_policies.py')
detail(2,'[F8] scopes.{scope}.detections[]',early_hit,'early_monitor/metrics.py:findings')
detail(2,'[F9] scopes.{scope}.durations[]',fields('key label samples unavailable mean median p95'),'early_monitor/metrics.py:durations')
detail(2,'[F10] stageDetections.*.cases[]',fields('eventId at user attempt reason evidence[] scope')+[s+'*' for s in fields('ruleVersion observed threshold windowSeconds restrictionId actionStatus appliedAt expiresAt releasedAt releaseReason')],'stage_detections.py · server_policies.py')

card(3,'runs',fields('_id target_kind status')+['run_config → [Q1]','suite_results[] → [Q2]','criterion_results[]','  criterion_id','  measured_value','  is_passed','started_at','finished_at'],source='test_runs/run_repository.py')
card(3,'answer_keys',['_id','entries[]','  session_key','  actor_type'],source='answer_key/answer_key_repository.py')
card(3,'bot_versions',fields('_id actor_type version_number profile')+['  behavior → [Q3]','  change_note','saved_at'],source='macro_bots/bot_profile_store.py')
card(3,'bot_types',fields('_id actor_type short_name display_name purpose category_id color_hex detection_signals[] detection_target_id status base_actor_type traffic_channel')+['initial_behavior → [Q3]','created_at','updated_at'],source='macro_bots/bot_type_store.py')
card(3,'archive_exports',fields('_id exported_at recipient reason run_ids[] collection_names[] bot_version_numbers')+['  {actor_type}'],source='qa_database/archive_export.py')
cfg=dataclass_fields('RunConfig')
config_lines=[]
for s in cfg:
    config_lines.append(s+'[]' if s=='suite_names' else s)
    if s=='traffic_mix': config_lines+=['  percent_by_type','    {actor_type}']
    if s=='bot_version_numbers': config_lines+=['  {actor_type}']
detail(3,'[Q1] runs.run_config',config_lines,'domain/models.py:run_config_to_dict')
detail(3,'[Q2] runs.suite_results[]',fields('suite_name outcome metrics')+['  {동적 metric_name}','message','details','  {Suite별 동적 키}','duration_sec'],'test_runs/run_repository.py')
detail(3,'[Q3] behavior / initial_behavior',dataclass_fields('SessionBehavior'),'domain/models.py · behavior_fields.py')

# Diagram layout: fixed-width schema cards, adaptive-height, column packing.
COLORS=['#087f8c','#2865b0','#7652ae','#b57426']
TITLES=['ticketguard_raw','ticketguard','ticketguard_flow','ticketguard_qa']
SUBS=['원본 · 10 collections','시스템 · 11 collections / 2 views','흐름 지표 · 10 collections / 2 views','QA 전용 · 5 collections / 독립 인스턴스']
CW=660; GAP=22; PAD=28; COLS=[2,3,4,2]
FONT=23; STEP=30; HEAD=74
def wrap_lines(lines):
    result=[]
    for s in lines:
        # Each field remains on its own line; only long labels wrap.
        indent=len(s)-len(s.lstrip())
        limit=43 if all(ord(c)<128 for c in s) else 30
        if len(s)>limit:
            parts=textwrap.wrap(s,limit,break_long_words=False,break_on_hyphens=False)
            result+=parts
        else:result.append(s)
    return result
def plan(cards,ncols):
    ys=[140]*ncols; placements=[]
    # Keep definitions grouped: collections first, views then expansions.
    ordered=sorted(enumerate(cards),key=lambda pair:({'collection':0,'inactive':0,'view':1,'detail':2}[pair[1]['kind']],pair[0]))
    for _,c in ordered:
        rows=wrap_lines(c['lines']); h=HEAD+len(rows)*STEP+42
        col=min(range(ncols),key=lambda i:ys[i]); placements.append((col*(CW+GAP)+PAD,ys[col],h,c,rows));ys[col]+=h+GAP
    return placements,max(ys)+75
plans=[plan(PANELS[i],COLS[i]) for i in range(4)]
widths=[n*(CW+GAP)-GAP+2*PAD for n in COLS]
# One landscape sheet; four adjacent DB areas, each with its own schema grid.
W=sum(widths)+3*GAP+80
panelH=max(p[1] for p in plans)
positions={};left=40
for i in range(4):
    positions[i]=(left,180,widths[i],panelH)
    left+=widths[i]+GAP
H=180+panelH+110
out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><rect width="100%" height="100%" fill="white"/><style>text{{font-family:Consolas,Malgun Gothic,monospace;fill:#243247}}</style>']
def rect(x,y,w,h,fill,stroke='none',dash=False):out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.5" '+('stroke-dasharray="9 6"' if dash else '')+'/>')
def txt(x,y,s,size=FONT,color='#243247',bold=False):out.append(f'<text x="{x}" y="{y}" font-size="{size}" style="fill:{color}" '+('font-weight="700"' if bold else '')+'>'+html.escape(s)+'</text>')
txt(42,70,'TicketGuard MongoDB 컬렉션·필드 구조',48,bold=True)
txt(44,117,'컬렉션 내부 필드 펼침  /  * 조건부 필드  /  [] 배열  /  { } 동적 키  /  중첩 객체 상세는 컬렉션 아님',25,'#64748b')
audit=[]
for i in [0,1,3,2]:
    px,py,pw,ph=positions[i];color=COLORS[i]
    rect(px,py,pw,ph,'#f7f9fc','#d7e0ea');rect(px,py,pw,105,color)
    txt(px+28,py+44,f'0{i+1}  {TITLES[i]}',35,'white',True);txt(px+28,py+80,SUBS[i],23,'white')
    for cx,cy,ch,c,rows in plans[i][0]:
        x,y=px+cx,py+cy;k=c['kind'];muted=k=='inactive';detail_=k=='detail'
        rect(x,y,CW,ch,'white' if not muted else '#f0f1f3','#b7c4d4' if k=='view' else '#d9e1eb',k=='view')
        rect(x,y,CW,HEAD,'#edf2f8' if detail_ else '#e6e9ee' if muted else color)
        title=c['title'];ts=24 if len(title)<39 else 21
        txt(x+18,y+32,title,ts,color if detail_ else '#687282' if muted else 'white',True)
        label={'collection':'COLLECTION','view':'VIEW · 별도 저장 없음','inactive':'INITIALIZATION ONLY','detail':'중첩 객체 상세 · 컬렉션 아님'}[k]
        txt(x+18,y+59,label,18,color if detail_ else '#687282' if muted else '#ffffff')
        for j,line in enumerate(rows):
            indent=len(line)-len(line.lstrip());special=line.startswith('[') or line.startswith('원본:') or '미확인' in line
            txt(x+20+indent*10,y+HEAD+30+j*STEP,line.lstrip(),21 if special else FONT,color if special or '→' in line else '#243247',special)
        if len(rows):
            last=y+HEAD+30+(len(rows)-1)*STEP
            assert last<y+ch-12
        assert x+CW<=px+pw and y+ch<=py+ph
        audit.append({'db':TITLES[i],**c,'x':x,'y':y,'w':CW,'h':ch})
    # no cross-panel relations: compact key references in a footer
    foot=['value 내부 JSON은 Binary · 하위 BSON 필드로 펼치지 않음','anomaly_events.eventId → clean_events._id  /  network_public.version → versions·series._id','기본 DB 이름 · 실제 대상은 FLOW_TARGET_MONGO_URI 설정','runs._id ↔ answer_keys._id  /  Suite 정답표: run_id/suite_name'][i]
    footrows=textwrap.wrap(foot,65,break_long_words=False)
    for fi,fline in enumerate(footrows):txt(px+25,py+ph-25-(len(footrows)-1-fi)*25,fline,20,'#64748b')
txt(44,H-55,'현재 소스 코드의 저장 문서 기준 · 조건부 필드 포함 · 실제 운영 데이터 미확인 · 참조는 논리적 연결이며 외래 키 제약 아님',23,'#64748b')
out.append('</svg>')
dest=ROOT/'artifacts'
(dest/'ticketguard-mongodb-fields.svg').write_text('\n'.join(out),encoding='utf-8')
(dest/'ticketguard-mongodb-fields-schema.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'width':W,'height':H,'cards':len(audit),'panel_heights':[p[1] for p in plans]}))
