/* 새 영역 내부에서만 선택 상태·공통 상세를 갱신한다. 기존 타임라인 선택과 독립적이다. */
(() => {
  'use strict';
  document.querySelectorAll('[data-stage-detection-root]').forEach(root => {
    const node=root.querySelector('[data-sd-data]');
    if(!node) return;
    let stages;
    try { stages=JSON.parse(node.textContent); } catch { return; }
    const metrics=new Map(stages.flatMap(stage => stage.metrics.map(metric => [metric.key,metric])));
    const buttons=Array.from(root.querySelectorAll('[data-sd-metric]'));
    const rows=root.querySelector('[data-sd-rows]');
    const write=(selector,text) => { root.querySelector(selector).textContent=text; };
    const value=(number,unit) => number===null ? '—' : `${number.toLocaleString('ko-KR')}${unit}`;
    function select(key) {
      const metric=metrics.get(key);
      if(!metric) return;
      buttons.forEach(button => button.setAttribute('aria-pressed',String(button.dataset.sdMetric===key)));
      root.querySelectorAll('[data-detection-stage]').forEach(stage => {
        const active=stage.dataset.detectionStage===metric.stage_key;
        stage.dataset.selected=String(active);
        stage.querySelector('.sd-selected-label').hidden=!active;
      });
      write('[data-sd-title]',`${metric.stage_label} · ${metric.label}`);
      write('[data-sd-detail-state]',metric.state);
      write('[data-sd-description]',metric.description);
      write('[data-sd-period]',metric.period);
      const events=metric.event_count===null ? '' : ` · 기준 충족 관측 ${value(metric.event_count,'건')}`;
      write('[data-sd-count]',`탐지 ${value(metric.value,metric.unit)}${events} · 실제 조치 ${value(metric.action_count,'건')}`);
      if(metric.rejected_count!==undefined && metric.rejected_count!==null) {
        const summary=root.querySelector('[data-sd-count]');
        summary.textContent+=` · 제한 중 거절 ${value(metric.rejected_count,'건')} · 제한 대상 ${value(metric.action_subjects,'개 계정')}`;
      }
      rows.replaceChildren();
      metric.cases.forEach(item => {
        const row=document.createElement('tr');
        [item.time,item.user||'미확인',item.attempt||'미확인',item.reason,
          item.evidence.join(' · '),item.action_status].forEach(text => {
          const cell=document.createElement('td'); cell.textContent=text; row.append(cell);
        });
        rows.append(row);
      });
      if(!metric.cases.length) {
        const row=document.createElement('tr'),cell=document.createElement('td');
        cell.colSpan=6;
        cell.textContent=metric.value===0 ? '관측 범위 내 탐지 없음' : metric.state;
        row.append(cell);rows.append(row);
      }
      const notes=[];
      if(metric.unknown_targets) notes.push(`대상 식별 부족 ${metric.unknown_targets}건 · 고유 시도 수에 임의 포함하지 않음`);
      if(metric.truncated) notes.push('목록은 최근 200개까지 · 탐지 수는 전체 관측 범위 집계');
      if(metric.action_count===null) notes.push('실제 조치 연결 근거 없음 · 미조치로 단정하지 않음');
      write('[data-sd-note]',notes.join(' / '));
    }
    buttons.forEach(button => button.addEventListener('click',() => select(button.dataset.sdMetric)));
    const initial=Array.from(metrics.values()).find(metric => metric.value!==null && metric.value>0)
      || Array.from(metrics.values()).find(metric => metric.value!==null)
      || Array.from(metrics.values()).find(metric => metric.implemented)
      || Array.from(metrics.values())[0];
    if(initial) select(initial.key);
  });
})();
