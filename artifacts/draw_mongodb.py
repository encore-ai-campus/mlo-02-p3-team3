from pathlib import Path
import re, html, textwrap
src=Path(r'C:/Users/Playdata/.codex/attachments/a608bb00-b723-4378-aef7-175c9492cbb4/붙여넣은 텍스트.txt').read_text(encoding='utf-8')
W,H=2800,1980
out=['<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" viewBox="0 0 2800 1980"><rect width="2800" height="1980" fill="white"/><style>text{font-family:Malgun Gothic,Arial,sans-serif;fill:#243247}.mono{font-family:Consolas,Malgun Gothic,monospace}.muted{fill:#65748b}</style>']
def rect(x,y,w,h,fill,stroke='none',dash=False):
 out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" '+('stroke-dasharray="6 4"' if dash else '')+'/>')
def text(x,y,s,size=18,color=None,mono=False,bold=False):
 out.append(f'<text x="{x}" y="{y}" font-size="{size}"'+(' class="mono"' if mono else '')+(f' style="fill:{color}"' if color else '')+(' font-weight="700"' if bold else '')+'>'+html.escape(s)+'</text>')
def lines(x,y,ss,size=17,step=25,mono=False):
 for s in ss: text(x,y,s,size,mono=mono);y+=step
 return y
text(55,66,'TicketGuard MongoDB 구조',40,bold=True)
text(57,103,'현재 소스 코드 기준 · 논리적 컬렉션 구조',21,'#65748b')
text(2740,65,'DATABASE REFERENCE  /  01',16,'#65748b')
# override right-aligned label position
out[-1]=out[-1].replace('x="2740"','x="2440"')
colors=['#087f8c','#2865b0','#7652ae','#b57426']
titles=['ticketguard_raw','ticketguard','ticketguard_flow','ticketguard_qa']
roles=['원본 DB · 이벤트 종류별 원본 메시지 보관','시스템 DB · 정제·격리 / 이상 탐지 / 네트워크 집계','흐름 지표 DB · 사용자 흐름 / 사전 모니터링','QA 전용 DB · 실행 / 정답표 / 봇 설정 / 내보내기']
starts=[src.index('[패널 '+str(i)) for i in range(1,5)]+[src.index('[전체 범례')]
refs=[[],['anomaly_events.eventId → clean_events._id','network_public.version → network_versions._id / network_series._id'],['flow_public.version → flow_versions._id / flow_series.version','flow_public._id → flow_versions.scope / flow_series.scope','early_public.version → early_versions._id'],['runs._id ↔ answer_keys._id  (Suite: run_id/suite_name)','runs.run_config.bot_version_numbers → bot_versions(actor_type, version_number)','bot_versions.actor_type → bot_types.actor_type  [사용자 정의 유형]','archive_exports.run_ids[] → runs._id']]
for i in range(4):
 x=55+(i%2)*1355;y=140+(i//2)*875;w=1325;h=850;c=colors[i]
 rect(x,y,w,h,'#ffffff','#d8e0e9');rect(x,y,w,104,c)
 text(x+25,y+39,f'0{i+1}  '+titles[i],29,'white',True,True)
 text(x+25,y+76,roles[i],19,'white')
 if i==3:text(x+w-175,y+38,'독립 인스턴스',17,'white',bold=True)
 section=src[starts[i]:starts[i+1]]
 yy=y+133
 text(x+22,yy,'컬렉션',16,'#65748b',bold=True);text(x+350,yy,'저장 내용',16,'#65748b',bold=True);text(x+660,yy,'주요 키 · 필드',16,'#65748b',bold=True);yy+=14
 group=''
 for line in section.splitlines():
  if line.startswith('그룹 '):
   group=line.split('—')[-1].strip();rect(x+16,yy,w-32,27,'#f0f4f8');text(x+25,yy+19,group,16,c,bold=True);yy+=27
  if not line.startswith('- ') or ' | ' not in line:continue
  cells=line[2:].split(' | ')
  name,desc=cells[:2];field=cells[2] if len(cells)>2 else ('공통 문서 구조 참조' if i==0 and name!='raw_events' else '')
  isview='read_current' in name or 'read_series' in name
  inactive=name=='raw_events'
  if inactive:rect(x+16,yy,w-32,29,'#f1f2f4')
  if isview:rect(x+16,yy,w-32,29,'#f4f8ff','#98b4da',True)
  # wrap lengthy ASCII fields to keep all text inside panel
  chunks=textwrap.wrap(field,73,break_long_words=False) or ['']
  rowh=max(27,len(chunks)*22+7)
  text(x+25,yy+21,('VIEW ' if isview else '')+name,17,'#697386' if inactive else c,True,True)
  text(x+350,yy+21,desc,17)
  lines(x+660,yy+21,chunks,15,22,True)
  yy+=rowh
 if i==0:
  yy+=16;rect(x+16,yy,w-32,220,'#f2f9f9');text(x+28,yy+31,'공통 문서 구조 · 분류 컬렉션',19,c,bold=True)
  lines(x+28,yy+64,['_id · source · topic · partition · offset · key · value · headers[]','timestampMs · timestampType · ingestedAt · routing'],18,29,True)
  lines(x+28,yy+133,['_id = SHA-256(source, topic, partition, offset)','value = 원본 JSON Binary · 업무 필드는 value 내부에 보관','인덱스: _id 고유 / (source, topic, partition, offset) 복합·비고유'],18,29)
 else:
  yy+=9;text(x+25,yy+19,'주요 참조  ·  논리적 연결',17,c,bold=True);yy+=43
  yy=lines(x+25,yy,refs[i],16,25,True)
 if i==1:
  yy+=10;text(x+25,yy,'clean_events · 주요 필드',17,c,bold=True);yy+=25
  yy=lines(x+25,yy,['eventId, eventType, source, userId, bookingAttemptId, showId, scheduleId,','result, errorCode, isSimulation, occurredAt, receivedAt, normalizedAt,','isAnomaly, evaluationStatus'],16,23,True)
  text(x+25,yy+3,'일부 예매 서버 이벤트: bookingId, quantity, totalAmount, seatType, seats',16)
 if i==2:
  text(x+25,yy+12,'scope: "all" 또는 [시뮬레이션 여부, 공연 ID, 회차 ID]의 JSON 문자열',16)
  text(x+25,yy+38,'flow_* / early_* 동일 대상 DB · 실제 DB는 FLOW_TARGET_MONGO_URI 설정',16)
  text(x+25,yy+64,'뷰는 동일 버전·범위를 결합 · 일부 이전 버전은 expiresAt 기반 TTL 정리',16)
 if i==3:
  yy+=17;rect(x+16,yy,w-32,145,'#fff8ef');text(x+28,yy+30,'문서 내장 구조 · 고유 인덱스',19,c,bold=True)
  lines(x+28,yy+62,['Suite 결과·합격 기준은 runs 내부 배열 · 별도 결과 컬렉션 없음','기본 제공 봇 유형은 코드 정의: bot_types 문서가 없을 수 있음','UNIQUE  bot_versions(actor_type, version_number) · bot_types(short_name)'],17,29)
 # ensure fit checked as metadata
out.append('<line x1="55" y1="1910" x2="2740" y2="1910" stroke="#d8e0e9"/>')
text(55,1944,'■ 컬렉션: 문서 저장     ▤ VIEW: 조회 정의·별도 저장 없음     ■ 회색: 초기화용·현재 적재 미사용',17,'#526176')
text(1550,1944,'참조는 외래 키 제약이 아님 · 실제 운영 DB 생성 여부·건수 미확인',17,'#526176')
out.append('</svg>')
Path('artifacts/ticketguard-mongodb.svg').write_text('\n'.join(out),encoding='utf-8')
print('SVG created')

