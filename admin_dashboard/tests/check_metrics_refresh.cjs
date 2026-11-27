const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
(async()=>{
  let tick, interval, resolve, requests=0, updates=0, replacements=0, fail=false;
  const status={dataset:{url:'/admin/metrics'},textContent:''};
  const stored={textContent:''};
  const host={contains:()=>false,querySelector:()=>null,querySelectorAll:()=>[],replaceChildren(){replacements++;}};
  const data={textContent:JSON.stringify({flow:{nodes:[]},anomaly:{detections:[]}}),remove(){}};
  const document={body:{dataset:{monitorMode:'live'}},activeElement:null,
    getElementById:id=>id==='monitor-refresh-status'?status:host,querySelector:()=>stored};
  const context={document,URL,AbortController,Event,Date,
    location:{origin:'https://example.test',search:'?scope=all&anomaly_mode=post',hash:'#anomaly'},
    window:{dispatchEvent(event){assert.equal(event.type,'monitor:updated');updates++;}},
    DOMParser:class{parseFromString(){return {querySelector:()=>data,querySelectorAll:()=>[1,2,3],body:{childNodes:['fresh panels']}};}},
    setInterval(fn,ms){tick=fn;interval=ms;},setTimeout:()=>1,clearTimeout(){},
    fetch(url,options){
      requests++;
      assert.equal(url.pathname,'/admin/metrics');
      assert.equal(url.search,'?scope=all&anomaly_mode=post');
      assert.equal(options.credentials,'same-origin');
      if(fail)return Promise.reject(new Error('offline'));
      return new Promise(r=>{resolve=r;});
    }};
  vm.runInNewContext(source,context);
  assert.equal(interval,1000);
  assert.equal(requests,0);
  const first=tick();await tick();assert.equal(requests,1,'overlapping request suppressed');
  resolve({ok:true,text:async()=>'<panels>'});await first;
  assert.equal(replacements,1);assert.equal(updates,1);
  assert.equal(JSON.parse(stored.textContent).flow.nodes.length,0);
  assert.equal(context.location.hash,'#anomaly');
  fail=true;await tick();assert.equal(replacements,1,'failed request preserves panels');
  fail=false;const retry=tick();resolve({ok:true,text:async()=>'<panels>'});await retry;
  assert.equal(replacements,2,'next tick recovers');
  document.body.dataset.monitorMode='preview';
  let previewTimer=false;
  vm.runInNewContext(source,{...context,setInterval(){previewTimer=true;}});
  assert.equal(previewTimer,false);
  console.log('1-second polling, overlap, filters, partial replacement, failure recovery and preview isolation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
