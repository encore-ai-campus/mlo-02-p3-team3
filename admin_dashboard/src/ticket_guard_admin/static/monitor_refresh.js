/* 실제 관리자 화면의 공개 지표만 1초마다 재조회한다. */
(() => {
  'use strict';
  const status=document.getElementById('monitor-refresh-status');
  if(!status || document.body.dataset.monitorMode!=='live') return;
  const host=document.getElementById('monitor-metrics');
  let active=false;
  let chartAt=Date.now(), chartScope=location.search;
  async function refresh() {
    if(active) return;
    active=true;
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),10000);
    try {
      const url=new URL(status.dataset.url,location.origin);
      const requestedSearch=location.search;
      url.search=requestedSearch;
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      if(!response.ok) throw new Error('metrics unavailable');
      const html=await response.text();
      if(location.search!==requestedSearch) return;
      const doc=new DOMParser().parseFromString(html,'text/html');
      const data=doc.querySelector('[data-metrics-data]');
      if(!data || doc.querySelectorAll('[data-preview-view]').length!==3) throw new Error('invalid metrics');
      const parsed=JSON.parse(data.textContent);
      if(!parsed.flow || !parsed.anomaly) throw new Error('missing metrics');
      // Preserve the focused filter without stopping metric updates.
      const focused=host.contains(document.activeElement) && document.activeElement.matches('input,select')
        ? document.activeElement : null;
      const focusIndex=focused ? [...host.querySelectorAll('input,select')].indexOf(focused) : -1;
      const controls=[...host.querySelectorAll('input,select')].map(e=>[e.name,e.value]);
      const details=[...host.querySelectorAll('details')].map(e=>e.open);
      const selections=['data-sd-metric','data-an-event','data-an-account'].map(attr=>{
        const e=host.querySelector(`[${attr}][aria-pressed="true"]`);
        return e ? [attr,e.getAttribute(attr),e.textContent] : null;
      });
      const scrolls=[...host.querySelectorAll('[class*="scroll"]')].map(e=>[e.scrollLeft,e.scrollTop]);
      const longCharts=[...host.querySelectorAll('.an-box.an-chart-box')];
      const scopeChanged=chartScope!==location.search;
      const refreshLong=scopeChanged || Date.now()-chartAt>=30000;
      if(!refreshLong) {
        const oldData=JSON.parse(document.querySelector('[data-monitor-data]').textContent || '{}');
        if(oldData.anomaly) parsed.anomaly.series=oldData.anomaly.series;
        doc.querySelectorAll('.an-box.an-chart-box').forEach((node,i)=>{
          if(longCharts[i]) node.replaceWith(longCharts[i]);
        });
      } else { chartAt=Date.now(); chartScope=location.search; }

      data.remove();
      document.querySelector('[data-monitor-data]').textContent=JSON.stringify(parsed);
      host.replaceChildren(...doc.body.childNodes);
      controls.forEach(([name,value])=>host.querySelectorAll('input,select').forEach(e=>{if(e.name===name)e.value=value;}));
      host.querySelectorAll('details').forEach((e,i)=>{e.open=details[i]||false;});
      window.dispatchEvent(new Event('monitor:updated'));
      selections.filter(Boolean).forEach(([attr,value,label])=>{
        const button=[...host.querySelectorAll(`[${attr}]`)].find(e=>attr==='data-an-account'?e.textContent===label:e.getAttribute(attr)===value);
        button?.click();
      });
      // The navigation listener must reveal the selected panel before focus.
      if(focusIndex>=0) host.querySelectorAll('input,select')[focusIndex]?.focus({preventScroll:true});
      host.querySelectorAll('[class*="scroll"]').forEach((e,i)=>{if(scrolls[i])[e.scrollLeft,e.scrollTop]=scrolls[i];});
      status.textContent=`1초 갱신 · 최근 10초 · 1초 집계 · 긴 이상 추이 30초 갱신 · 마지막 조회 ${new Date().toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})} KST`;
    } catch {
      status.textContent='자동 갱신 실패 · 이전 화면 유지 · 다음 주기에 재시도';
    } finally {
      clearTimeout(timeout);
      active=false;
    }
  }
  setInterval(refresh,1000);
})();
