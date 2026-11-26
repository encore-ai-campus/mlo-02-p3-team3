/* 관리자 이상 관측 · DOM 텍스트로 근거 표시, 서버로 쓰기 요청 없음 */
(() => {
  'use strict';
  let lifecycle;
  function init() {
  lifecycle?.abort();
  lifecycle=new AbortController();
  const node=document.querySelector('[data-monitor-data]');
  if(!node)return;
  const data=JSON.parse(node.textContent).anomaly;
  if(!data)return;
  const $=id=>document.getElementById(id);
  const time=value=>new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(Number(value)*1000));
  const labels={booking_confirm_request:'구매 요청',booking_confirm_success:'구매 성공',server_request:'구매 요청·응답 서버 관측',queue_enter:'대기열 진입',queue_register:'대기열 등록'};
  document.querySelectorAll('[data-an-time]').forEach(cell=>cell.textContent=time(cell.dataset.anTime));
  function timeline(id,rows){
    $(id).replaceChildren(...rows.map(row=>{const li=document.createElement('li'),at=document.createElement('time');at.textContent=time(row.at);li.append(at,document.createTextNode(labels[row.kind]||row.kind));return li;}));
  }
  const events=[...document.querySelectorAll('[data-an-event]')];
  events.forEach(button=>button.addEventListener('click',()=>{
    events.forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    const row=data.detections.find(r=>r.eventId===button.dataset.anEvent);
    $('an-timeline-title').textContent=`계정 ${row.user} 타임라인`;
    timeline('an-timeline',data.timelines[row.eventId]||row.evidence);
  }));
  const accounts=[...document.querySelectorAll('[data-an-account]')];
  accounts.forEach(button=>button.addEventListener('click',()=>{
    accounts.forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    const row=data.accounts[Number(button.dataset.anAccount)];
    $('an-account-title').textContent=`계정 ${row.user} 상세`;
    $('an-speed').textContent=row.durations.length?`${row.durations.map(v=>v.toFixed(3)).join(', ')}초 · 대기열 진입 → 구매 성공`:'완료 시간 근거 부족 또는 정책 미설정';
    $('an-route').textContent='정확한 요청·응답과 구매의 연결 계약 미수집';
    $('an-results').textContent=row.bookings.map(b=>`${b.id} ${b.quantity}매 (${b.show||'공연 미확인'} / ${b.schedule||'회차 미확인'})`).join(' · ');
    timeline('an-purchase-timeline',row.evidence);
  }));
  function chart(){
    const host=$('an-chart');if(!host||!host.getBoundingClientRect().width)return;
    const signature=JSON.stringify([data.series,data.policy,host.clientWidth]);
    if(host.dataset.signature===signature)return;
    host.dataset.signature=signature;
    const points=data.series;if(!points.length){host.textContent='표시 가능한 관측 자료 없음';host.className='an-chart-empty';return;}
    const NS='http://www.w3.org/2000/svg',make=(tag,attrs,text)=>{const e=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text)e.textContent=text;return e;};
    const width=Math.max(300,host.clientWidth),height=165,left=35,right=15,top=15,bottom=30;
    const max=Math.max(1,...points.flatMap(p=>[p.overlap,p.retry])),x=i=>left+i*(width-left-right)/Math.max(1,points.length-1),y=v=>height-bottom-v*(height-top-bottom)/max;
    const svg=make('svg',{viewBox:`0 0 ${width} ${height}`,class:'an-chart',role:'img','aria-label':'분 단위 이상 이벤트 수 · 미관측 구간은 연결하지 않음'});
    [0,max].forEach(v=>{svg.append(make('line',{x1:left,x2:width-right,y1:y(v),y2:y(v)}),make('text',{x:5,y:y(v)+4},String(v)));});
    ['overlap','retry'].forEach((key,index)=>{
      const enabled=data.policy[key]!=null;
      let segment=[];const flush=()=>{if(segment.length)svg.append(make('polyline',{points:segment.join(' '),stroke:index?'#7faeff':'#1858ff','stroke-dasharray':index?'4 3':'none'}));segment=[];};
      points.forEach((p,i)=>{if(!p.observed||!enabled||!Number.isFinite(p[key])){flush();return;}segment.push(`${x(i)},${y(p[key])}`);const circle=make('circle',{cx:x(i),cy:y(p[key]),r:3,fill:index?'#7faeff':'#1858ff'});circle.append(make('title',{},`${p.at}: ${key} ${p[key]}건`));svg.append(circle);});flush();
    });
    [0,Math.floor(points.length/2),points.length-1].forEach(i=>svg.append(make('text',{x:x(i),y:height-5,'text-anchor':i===0?'start':i===points.length-1?'end':'middle'},new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(points[i].at)))));
    host.replaceChildren(svg);
  }
  document.querySelectorAll('[data-an-mode]').forEach(button=>button.addEventListener('click',()=>{
    const mode=button.dataset.anMode;document.querySelectorAll('[data-an-panel]').forEach(panel=>panel.hidden=panel.dataset.anPanel!==mode);
    document.querySelectorAll('[data-an-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    const url=new URL(location.href);if(mode==='post')url.searchParams.set('anomaly_mode','post');else url.searchParams.delete('anomaly_mode');history.replaceState(null,'',url);chart();
  }));
  if(new URL(location.href).searchParams.get('anomaly_mode')==='post')document.querySelector('[data-an-mode="post"]').click();
  if(events.length)events[0].click();if(accounts.length)accounts[0].click();
  window.addEventListener('hashchange',chart,{signal:lifecycle.signal});window.addEventListener('resize',chart,{signal:lifecycle.signal});chart();
  }
  window.addEventListener('monitor:updated',init);
  init();
})();
