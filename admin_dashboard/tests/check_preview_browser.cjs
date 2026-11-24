/*
[홍석환] 기존 인증 게이트웨이를 통한 시안 브라우저 검사
관점 전환·상세·검색·작은 화면을 확인하고 샘플 화면 캡처를 저장한다.
*/
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(__dirname,'../..');
function settings() {
  const result={};
  for(const line of fs.readFileSync(path.join(root,'.env'),'utf8').split(/\r?\n/)) {
    const match=line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if(match) result[match[1]]=match[2].replace(/^(['"])(.*)\1$/,'$2');
  }
  return result;
}
(async()=>{
  const env=settings(), base=process.env.PREVIEW_BASE_URL || 'http://localhost:8000';
  const username=env.DEMO_ADMIN_USERNAME, password=env.DEMO_ADMIN_PASSWORD;
  assert(username && password, '관리자 로그인 설정 필요');
  const browser=await chromium.launch({channel:process.env.PREVIEW_BROWSER_CHANNEL || 'msedge',headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1600,height:1000},deviceScaleFactor:1});
    await context.request.get(base+'/login/');
    const csrf=(await context.cookies()).find(cookie=>cookie.name==='csrftoken');
    assert(csrf, 'CSRF 쿠키 누락');
    const login=await context.request.post(base+'/api/action/',{headers:{'X-CSRFToken':csrf.value,'Referer':base+'/login/'},
      data:{action:'login',username,password}});
    assert.equal(login.status(),200,'기존 게이트웨이 관리자 인증 실패');
    const page=await context.newPage(); const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const output=path.join(root,'output/admin-preview'); fs.mkdirSync(output,{recursive:true});
    const response=await page.goto(base+'/admin/preview#flow'); assert.equal(response.status(),200);
    await page.locator('.mp-stage').first().waitFor();
    assert.equal(await page.locator('.mp-stage').count(),6);
    assert.equal(await page.locator('#mp-presence-chart svg').count(),1);
    await page.getByRole('button',{name:/대기열, 현재/}).click();
    assert.match(await page.locator('#mp-flow-detail').innerText(),/표본/);
    await page.screenshot({path:path.join(output,'flow-desktop.png'),fullPage:true});
    await page.getByRole('link',{name:'네트워크 상태',exact:true}).click();
    await page.getByRole('heading',{name:'네트워크 상태',exact:true}).waitFor();
    assert.equal(await page.locator('#mp-requests-chart svg').count(),1);
    await page.screenshot({path:path.join(output,'network-desktop.png'),fullPage:true});
    await page.getByRole('link',{name:'이상탐지',exact:true}).click();
    await page.getByRole('button',{name:'VIEW-008 상세 보기',exact:true}).click();
    assert.equal(await page.locator('.mp-subject-id').innerText(),'VIEW-008');
    await page.locator('#mp-subject-search').fill('없는대상');
    assert.match(await page.locator('#mp-subject-count').innerText(),/^0개/);
    await page.locator('#mp-subject-search').fill('');
    await page.locator('#mp-signal-filter').selectOption('captcha');
    assert((await page.locator('#mp-subjects button').count())<8);
    await page.locator('#mp-signal-filter').selectOption('all');
    await page.screenshot({path:path.join(output,'anomaly-desktop.png'),fullPage:true});
    await page.goBack();
    assert(page.url().endsWith('#network'));
    await page.goto(base+'/admin/preview#anomaly');
    await page.getByRole('heading',{name:'이상탐지',exact:true}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'anomaly-mobile.png'),fullPage:true});
    await page.getByRole('link',{name:'사용자 흐름',exact:true}).click();
    await page.screenshot({path:path.join(output,'flow-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'페이지 전체 가로 넘침');
    assert.deepEqual(errors,[],'브라우저 실행 오류');
    const live=await context.request.get(base+'/admin/');
    assert.equal(live.status(),200);
    assert(!(await live.text()).includes('monitor-preview-data'),'실제 화면을 시안으로 대체함');
    console.log(JSON.stringify({views:3,stages:6,desktopWidth:1600,mobileWidth:390,errors:errors.length,
      authentication:'기존 게이트웨이 관리자 인증',screenshots:output}));
  } finally { await browser.close(); }
})().catch(error=>{ console.error(error.message); process.exitCode=1; });
