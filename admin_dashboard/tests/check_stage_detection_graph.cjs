/* 신규 영역만 사용하는 선택·공통 상세·안전한 텍스트 삽입 검사. 브라우저·네트워크 사용 없음. */
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');

function element(tag='div') {
  const result={tag,dataset:{},attributes:{},children:[],textContent:'',hidden:false,
    append(...children){this.children.push(...children);},
    replaceChildren(...children){this.children=[...children];},
    setAttribute(key,value){this.attributes[key]=value;},
    addEventListener(name,callback){this[name]=callback;}};
  Object.defineProperty(result,'innerHTML',{set(){throw new Error('Unsafe HTML insertion');}});
  return result;
}

const stageKeys=['login','queue','selection','confirm'];
const malicious='<img src=x onerror=alert(1)>';
const data=stageKeys.map((stage,index)=>({key:stage,metrics:[{
  key:'metric-'+index,stage_key:stage,stage_label:stage,label:'label-'+index,
  value:index===1?2:null,unit:'건',event_count:index===1?3:null,action_count:index===1?1:null,
  state:index===0?'미구현':index===1?'관측 범위':'조회 실패',description:'description-'+index,
  period:'period',unknown_targets:0,truncated:false,implemented:index!==0,
  cases:index===1?[{time:'17:00:00',user:malicious,attempt:'attempt',reason:'reason',
                     evidence:['evidence'],action_status:'적용'}]:[]
}]}));
const selectors=['[data-sd-title]','[data-sd-detail-state]',
  '[data-sd-count]','[data-sd-period]','[data-sd-rows]'];
const nodes=Object.fromEntries(selectors.map(key=>[key,element()]));
nodes['[data-sd-data]']={textContent:JSON.stringify(data)};
const buttons=data.map(stage=>Object.assign(element('button'),{dataset:{sdMetric:stage.metrics[0].key}}));
const stages=stageKeys.map(key=>{
  const selected=element('span');
  return Object.assign(element('section'),{dataset:{detectionStage:key},selected,
    querySelector(selector){assert.equal(selector,'.sd-selected-label');return selected;}});
});
const root={querySelector(selector){assert.ok(nodes[selector],`Unexpected selector ${selector}`);return nodes[selector];},
  querySelectorAll(selector){
    if(selector==='[data-sd-metric]')return buttons;
    if(selector==='[data-detection-stage]')return stages;
    throw new Error(`Unexpected root selector ${selector}`);
  }};
const previousTimeline={selected:'existing-selection'};
const document={querySelectorAll(selector){assert.equal(selector,'[data-stage-detection-root]');return [root];},createElement:element};
const listeners={};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{document,window:{addEventListener(name,fn){listeners[name]=fn;}}});
assert.equal(typeof listeners['monitor:updated'],'function');
assert.equal(buttons[1].attributes['aria-pressed'],'true');
assert.equal(stages[1].dataset.selected,'true');
assert.equal(stages.filter(stage=>!stage.selected.hidden).length,1);
assert.equal(nodes['[data-sd-rows]'].children[0].children.length,6);
assert.equal(nodes['[data-sd-rows]'].children[0].children[1].textContent,malicious);
buttons[0].click();
assert.equal(nodes['[data-sd-rows]'].children[0].children[0].colSpan,6);
assert.equal(nodes['[data-sd-rows]'].children[0].children[0].textContent,'미구현');
assert.equal(stages[0].dataset.selected,'true');
assert.equal(buttons[1].attributes['aria-pressed'],'false');
buttons[3].click();
assert.equal(nodes['[data-sd-rows]'].children[0].children[0].textContent,'조회 실패');
assert.ok(nodes['[data-sd-count]'].textContent.includes('—'));
assert.deepEqual(previousTimeline,{selected:'existing-selection'});
process.stdout.write('Four-stage selection, common detail, missing states, isolation and text safety passed.\n');
