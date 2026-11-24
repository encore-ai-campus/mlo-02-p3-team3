const fs=require('node:fs');
const base='C:/Users/Playdata/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/';
const {instance}=require(base+'@viz-js/viz');
const sharp=require(base+'sharp');
const records=JSON.parse(fs.readFileSync('artifacts/ticketguard-mongodb-fields-schema.json','utf8'));
const colors=['#087f8c','#2865b0','#7652ae','#b57426'];
const dbs=['ticketguard_raw','ticketguard','ticketguard_flow','ticketguard_qa'];
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const edges=[];
function rel(db,s,sf,t,tf,label,mode='ref',cardinality='unknown') {edges.push({db,s,sf,t,tf,label,mode,cardinality});}
rel(1,'anomaly_events','eventId','clean_events','_id','eventId = _id','ref','many');
rel(1,'anomaly_events','evidence[]','anomaly_observations','_id','evidence[].eventId = _id');
rel(1,'network_public','version','network_versions','_id','version = _id','ref','one');
rel(1,'network_public','version','network_series','_id','version = _id','ref','one');
rel(2,'flow_public','version','flow_versions','_id','(version, _id) = (_id, scope)','ref','one');
rel(2,'flow_public','version','flow_series','version','(version, _id) = (version, scope)','ref','one');
rel(2,'early_public','version','early_versions','_id','version = _id','ref','one');
rel(2,'early_health','version*','early_versions','_id','version* = _id','ref','one');
rel(2,'early_detections','eventId','early_observations','_id','observed: eventId = _id','ref','many');
rel(2,'early_detections','id','early_observations','_id','server: id = _id');
rel(2,'early_actions','eventId','early_observations','_id','eventId = _id','ref','many');
rel(3,'answer_keys','_id','runs','_id','Run: _id = run_id / Suite: run_id/suite_name','ref','many');
rel(3,'[Q1] runs.run_config','bot_version_numbers','bot_versions','actor_type','{actor_type}:version -> (actor_type,version_number)');
rel(3,'bot_versions','actor_type','bot_types','actor_type','custom type only; built-in: code default');
rel(3,'archive_exports','run_ids[]','runs','_id','run_ids[] = _id (array reference)');
// Views: each definition explicitly joins pointer and the selected version.
for(const [d,p,view,target] of [[1,'network_public','network_read_current','network_versions'],[1,'network_public','network_read_series','network_series'],[2,'flow_public','flow_read_current','flow_versions'],[2,'flow_public','flow_read_series','flow_series']]) {
 rel(d,view,'version',p,'version','VIEW ON','view');
 rel(d,view,'result',target,'_id',d===2?'LOOKUP version + scope':'LOOKUP version = _id','view');
}
// Explicit expansions are embedded fields, never independent collections.
for(let d=0;d<4;d++) {
 const subset=records.filter(r=>r.db===dbs[d]);
 for(const r of subset) for(const line of r.lines) {
  const m=line.match(/→ \[([SFQ]\d+)\]/);if(!m)continue;
  const target=subset.find(x=>x.title.startsWith('['+m[1]+']'));
  if(target)rel(d,r.title,line,target.title,null,'EMBEDDED','embedded');
 }
}
// Same in-document structures reused in publications.
rel(1,'anomaly_public','detections[]','anomaly_events','_id','embedded copy; evaluationStatus excluded','embedded');
rel(2,'[F5] data.scopes.{scope}','actions[]','early_actions','_id','embedded copy; _id excluded','embedded');
function rowIndex(r,field){if(field===null)return null;field=field.trim();let ix=r.lines.findIndex(x=>x.trim()===field);if(ix<0)ix=r.lines.findIndex(x=>x.trim().startsWith(field));if(ix<0)throw Error('Missing field '+r.title+': '+field);return ix;}
function nodeLabel(r,id,d){
 const titleColor=r.kind==='inactive'?'#7b8490':colors[d];
 const isdetail=r.kind==='detail';
 let content=`<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="7" COLOR="#c5cfdd" BGCOLOR="white"><TR><TD ALIGN="LEFT" BGCOLOR="${titleColor}"><FONT COLOR="white" POINT-SIZE="22"><B>${esc(r.title)}</B></FONT></TD></TR><TR><TD ALIGN="LEFT" BGCOLOR="#edf2f7"><FONT POINT-SIZE="14">${r.kind==='view'?'VIEW':isdetail?'EMBEDDED · 컬렉션 아님':r.kind==='inactive'?'초기화용':'COLLECTION'}</FONT></TD></TR>`;
 r.lines.forEach((line,i)=>{
  let text=line.trim(),indent=line.length-line.trimStart().length;
  let flags=[];
  if(text==='_id')flags.push('ID');
  if((r.title==='bot_versions'&&['actor_type','version_number'].includes(text))||(r.title==='bot_types'&&text==='short_name'))flags.push(r.title==='bot_versions'?'UQ1':'UQ');
  if(edges.some(e=>e.db===d&&e.s===r.title&&rowIndex(r,e.sf)===i&&e.mode==='ref'))flags.push('REF');
  if(text.startsWith('원본:'))text=text.replace('원본:','SOURCE:');
  const color=flags.includes('REF')?colors[d]:'#27364b';
  content+=`<TR><TD PORT="p${i}" ALIGN="LEFT"><FONT POINT-SIZE="18" COLOR="${color}">${'&#160;'.repeat(indent*2)}${esc(text)}${flags.length?' <B>['+flags.join(' · ')+']</B>':''}</FONT></TD></TR>`;
 });return content+'</TABLE>';
}
async function main(){
 const viz=await instance();let panels=[];const audit=[];
 for(let d=0;d<4;d++){
  const subset=records.filter(r=>r.db===dbs[d]);const ids=new Map(subset.map((r,i)=>[r.title,'n'+i]));
  let dot='digraph G { graph [rankdir=LR, pack=true, packmode="array_u2", bgcolor="white", nodesep=0.65, ranksep=1.9, pad=0.4, splines=polyline, outputorder=edgesfirst, fontname="Malgun Gothic"]; node [shape=plain,fontname="Consolas"]; edge [fontname="Malgun Gothic",fontsize=14,penwidth=1.6,arrowsize=0.9];\n';
  subset.forEach(r=>dot+=`${ids.get(r.title)} [label=<${nodeLabel(r,ids.get(r.title),d)}>];\n`);
  for(const e of edges.filter(e=>e.db===d)){
   const s=subset.find(r=>r.title===e.s),t=subset.find(r=>r.title===e.t);const si=rowIndex(s,e.sf),ti=rowIndex(t,e.tf);
   let attrs=`label="${e.label}",color="${e.mode==='embedded'?'#9ba7b6':e.mode==='view'?'#42859b':colors[d]}",fontcolor="#475569"`;
   if(e.mode==='ref'){
    attrs+=',style=dashed';
    if(e.cardinality==='many')attrs+=',dir=both,arrowtail=odotcrow,arrowhead=odottee';
    else if(e.cardinality==='one')attrs+=',dir=both,arrowtail=odottee,arrowhead=odottee';
    else attrs+=',arrowhead=vee,xlabel="카디널리티 미확정"';
   }else if(e.mode==='embedded')attrs+=',arrowhead=odiamond,penwidth=0.9';
   else attrs+=',style=dotted,arrowhead=normal';
   dot+=`${ids.get(s.title)}:p${si}:e -> ${ids.get(t.title)}${ti===null?'':':p'+ti}:w [${attrs}];\n`;
   audit.push(e);
  }
  // Disconnected raw collections are laid out in five paired rows.
  if(d===0){for(let i=0;i<subset.length;i+=2){dot+=`{rank=same; n${i}; n${i+1};}\n`;if(i+2<subset.length)dot+=`n${i} -> n${i+2} [style=invis];\n`;}}
  dot+='}';fs.writeFileSync(`artifacts/mongodb-erd-${d}.dot`,dot);
  const svg=viz.renderString(dot,{format:'svg'});fs.writeFileSync(`artifacts/mongodb-erd-${d}.svg`,svg);
  const match=svg.match(/viewBox="([^"]+)"/);const [,,w,h]=match[1].split(/\s+/).map(Number);
  panels.push({svg:svg.slice(svg.indexOf('<svg')).replace(/<\?xml[^>]*>/g,''),w,h});
 }
 // Scale DB areas to a shared panel height; retain actual field coordinates inside each SVG.
 const ph=Math.max(...panels.map(p=>p.h))+145,header=110,gap=45;panels.forEach(p=>p.scale=1);
 const widths=panels.map(p=>p.w*p.scale+40);const W=widths.reduce((a,b)=>a+b)+gap*5,H=ph+285;
 let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><rect width="100%" height="100%" fill="white"/><style>text{font-family:Consolas,Malgun Gothic,monospace}</style><text x="45" y="64" font-size="44" font-weight="700" fill="#203149">TicketGuard MongoDB ERD</text><text x="45" y="108" font-size="23" fill="#52667d">ID 식별 키 · UQ 고유 인덱스 / UQ1 복합 고유 키 · REF 논리적 참조 · * 조건부 · [] 배열 · EMBEDDED 중첩 · VIEW 조회 정의</text>`;
 let x=gap;
 panels.forEach((p,d)=>{
  svg+=`<rect x="${x}" y="150" width="${widths[d]}" height="${ph}" rx="15" fill="white" stroke="#cbd5e1"/><rect x="${x}" y="150" width="${widths[d]}" height="88" rx="12" fill="${colors[d]}"/><text x="${x+23}" y="204" font-size="32" fill="white" font-weight="700">${dbs[d]}${d===3?' · 독립 인스턴스':''}</text>`;
  svg+=p.svg.replace(/<svg[^>]*>/,`<svg x="${x+20}" y="255" width="${p.w*p.scale}" height="${p.h*p.scale}" viewBox="0 0 ${p.w} ${p.h}">`);x+=widths[d]+gap;
 });
 svg+=`<text x="45" y="${H-72}" font-size="22" fill="#52667d">점선: 논리적 참조 / 갈퀴: 논리적 다수 / ○|: 대상 0..1 / |: 1 / 가는 실선: EMBEDDED / 청록 점선: VIEW</text><text x="45" y="${H-32}" font-size="22" fill="#52667d">현재 소스 코드 기준 · 외래 키 제약 아님 · 실제 운영 데이터 미확인 · FLOW_TARGET_MONGO_URI에 따라 흐름 DB 대상 변경</text></svg>`;
 fs.writeFileSync('artifacts/ticketguard-mongodb-erd.svg',svg);fs.writeFileSync('artifacts/ticketguard-mongodb-erd-relations.json',JSON.stringify(audit,null,2));
 await sharp(Buffer.from(svg),{limitInputPixels:false}).resize({width:14000}).png().toFile('artifacts/ticketguard-mongodb-erd.png');
 await sharp('artifacts/ticketguard-mongodb-erd.png',{limitInputPixels:false}).resize(2400).toFile('artifacts/mongodb-erd-preview.png');
 console.log(JSON.stringify({W,H,panels:panels.map(p=>({w:p.w,h:p.h,scale:p.scale})),relations:audit.length}));
}
main().catch(e=>{console.error(e);process.exitCode=1});





