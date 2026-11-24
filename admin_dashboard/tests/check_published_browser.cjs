/*
[홍석환] 실제 공개 지표 화면의 브라우저 검사
운영 조회 상태와 격리된 정상 자료를 구분해 인증·그래프·모바일·시안 보존을 확인한다.
*/
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(__dirname,'../..');
(async()=>{
  const env={};
  for(const line of fs.readFileSync(path.join(root,'.env'),'utf8').split(/\r?\n/)) {
    const match=line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if(match) env[match[1]]=match[2].replace(/^(['"])(.*)\1$/,'$2');
  }
  const browser=await chromium.launch({channel:process.env.PREVIEW_BROWSER_CHANNEL || 'msedge',headless:true});
  try {
    const base=process.env.PREVIEW_BASE_URL || 'http://localhost:8000';
    const context=await browser.newContext({viewport:{width:1600,height:1000}});
    await context.request.get(base+'/login/');
    const csrf=(await context.cookies()).find(c=>c.name==='csrftoken'); assert(csrf);
    const login=await context.request.post(base+'/api/action/',{headers:{'X-CSRFToken':csrf.value,'Referer':base+'/login/'},data:{action:'login',username:env.DEMO_ADMIN_USERNAME,password:env.DEMO_ADMIN_PASSWORD}});
    assert.equal(login.status(),200,'관리자 인증 실패');
    const page=await context.newPage(), errors=[]; page.on('pageerror',e=>errors.push(e.message));
    const output=path.join(root,'output/admin-live'); fs.mkdirSync(output,{recursive:true});
    const actual=await page.goto(base+'/admin/'); assert.equal(actual.status(),200);
    await page.locator('#monitor-live-data').waitFor({state:'attached'});
    const actualState=await page.locator('[data-flow-state]').getAttribute('data-flow-state').catch(()=> 'published');
    assert.equal(await page.locator('#monitor-preview-data').count(),0);
    await page.screenshot({path:path.join(output,'actual-state.png'),fullPage:true});
    await page.getByRole('link',{name:'네트워크 상태',exact:true}).click();
    await page.getByRole('heading',{name:'구성 검토 중'}).first().waitFor();
    assert.equal(await page.locator('[data-preview-view="network"] .mp-compact-kpi').count(),0);
    await page.getByRole('link',{name:'이상탐지',exact:true}).click();
    assert.equal(await page.locator('[data-preview-view="anomaly"] #mp-subjects').count(),0);
    await page.goBack(); assert(page.url().endsWith('#network'));
    await page.route(base+'/admin/admin-static/*',route=>route.fulfill({path:path.join(root,'admin_dashboard/src/ticket_guard_admin/static',new URL(route.request().url()).pathname.split('/').pop())}));
    await page.route(base+'/admin/preview',route=>route.fulfill({path:path.join(root,'.venv/preview-fixture.html'),contentType:'text/html; charset=utf-8'}));
    await page.route(base+'/admin/',route=>route.fulfill({path:path.join(root,'.venv/published-fixture.html'),contentType:'text/html; charset=utf-8'}));
    await page.goto('about:blank');
    await page.goto(base+'/admin/#flow'); await page.locator('.mp-stage').first().waitFor();
    assert.equal(await page.locator('.mp-stage').count(),7);
    assert.equal(await page.locator('#mp-presence-chart svg').count(),1);
    await page.getByRole('button',{name:/기타·미확인, 현재/}).focus(); await page.keyboard.press('Enter');
    assert.match(await page.locator('#mp-flow-detail').innerText(),/표본 0건/);
    await page.getByRole('button',{name:/취소 · 고유 이동/}).click();
    assert.match(await page.locator('#mp-flow-detail').innerText(),/전환율 5.0%/);
    assert((await page.locator('body').innerText()).includes('브라우저 세션'));
    assert(!(await page.locator('body').innerText()).includes('샘플 데이터'));
    await page.locator('#mp-booking-chart circle').last().focus();
    assert.match(await page.locator('#mp-booking-chart .mp-chart-readout').innerText(),/진행 중 4건/);
    await page.locator('#mp-booking-chart circle').first().focus();
    assert.match(await page.locator('#mp-booking-chart .mp-chart-readout').innerText(),/시작 시도 없음/);
    await page.locator('#mp-booking-chart circle').nth(1).focus();
    assert.match(await page.locator('#mp-booking-chart .mp-chart-readout').innerText(),/미관측/);
    await page.screenshot({path:path.join(output,'isolated-published-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'isolated-published-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.unroute(base+'/admin/');
    await page.goto(base+'/admin/preview#flow'); await page.locator('.mp-stage').first().waitFor();
    assert.equal(await page.locator('.mp-stage').count(),6);
    const legacy=await context.request.get(base+'/admin/db'); assert.equal(legacy.status(),200);
    assert((await legacy.text()).includes('screen') || (await legacy.text()).includes('DB 조회 실패'));
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({actualState,isolatedStages:7,desktop:1600,mobile:390,errors:errors.length,previewPreserved:true,
      fixtureNote:'정상 그래프는 메모리 공개 문서의 격리 렌더링이며 운영 DB에 적재하지 않음',screenshots:output}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
