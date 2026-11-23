"""기존 게이트웨이에서 관리자 로그인·시안 전환·상세·반응형 검사. DB 시험 쓰기 없음."""
import os
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright


def run():
    root = Path(__file__).resolve().parents[2]
    values = dotenv_values(root / '.env')
    output = Path(os.environ['TEMP']) / 'ticketguard-anomaly-review'
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport=dict(width=1820, height=1000))
        base = 'http://localhost:8000'
        context.request.get(base+'/login/')
        csrf = next(c['value'] for c in context.cookies() if c['name']=='csrftoken')
        response = context.request.post(base+'/api/action/', headers={'X-CSRFToken':csrf, 'Origin':base},
            data={'action':'login','username':values['DEMO_ADMIN_USERNAME'],'password':values['DEMO_ADMIN_PASSWORD']})
        assert response.status == 200, 'Administrator login failed'
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        measure = '''() => {const value=s=>{const e=document.querySelector(s),c=getComputedStyle(e),r=e.getBoundingClientRect();return {height:r.height,width:r.width,font:c.fontSize,line:c.lineHeight}};return {bar:value('.top-bar'),logo:value('.brand-mark svg'),name:value('.brand-name'),english:value('.brand-english'),menu:value('.main-tabs a'),icon:value('.main-tabs a svg')};}'''
        badge_measure = '''() => {const badge=document.querySelector('.brand-badge'),b=badge.getBoundingClientRect(),home=document.querySelector('.main-tabs a').getBoundingClientRect();return {width:b.width,gap:home.left-b.right,homeLeft:home.left,linked:!!badge.closest('a'),homeLink:document.querySelector('a.brand').getAttribute('href')};}'''
        page.goto(base+'/qa/', wait_until='networkidle')
        home_header = page.evaluate(measure)
        home_badge = page.evaluate(badge_measure)
        assert not home_badge['linked'] and home_badge['width']==68 and home_badge['homeLink']=='/qa/'
        page.locator('.top-bar').screenshot(path=str(output/'header-home.png'))
        page.goto(base+'/qa/runs/new', wait_until='networkidle')
        assert page.evaluate(measure)==home_header, 'QA header differs from administrator home'
        assert page.evaluate(badge_measure)==home_badge, 'QA badge spacing differs from home'
        before_badge_click = page.url
        page.locator('.brand-badge').click()
        assert page.url==before_badge_click, 'Badge must not navigate'
        page.locator('.top-bar').screenshot(path=str(output/'header-qa.png'))
        page.locator('a.brand').click()
        assert page.url.rstrip('/')==base+'/qa', 'Brand logo must return to administrator home'
        page.goto(base+'/admin/preview#anomaly', wait_until='networkidle')
        db_header = page.evaluate(measure)
        assert page.evaluate(badge_measure)==home_badge, 'DB badge/menu alignment differs from QA'
        for key in ('logo','name','english','menu','icon'):
            assert db_header[key]==home_header[key], f'DB header scale differs: {key}: {db_header[key]} vs {home_header[key]}'
        assert page.locator('.brand-name').evaluate('(e)=>getComputedStyle(e).paddingTop')=='15px'
        assert db_header['bar']['height']==home_header['bar']['height']==76
        page.locator('.top-bar').screenshot(path=str(output/'header-db.png'))
        panel = page.locator('[data-preview-view="anomaly"]')
        assert panel.is_visible()
        assert page.locator('[data-an-panel="pre"]').is_visible()
        page.locator('[data-an-event="c"]').click()
        assert 'C' in page.locator('#an-timeline-title').inner_text()
        page.screenshot(path=str(output/'preview-pre-desktop.png'), full_page=True)
        page.get_by_role('button', name='사후 처리', exact=True).click()
        page.locator('[data-an-account="1"]').click()
        assert 'B' in page.locator('#an-account-title').inner_text()
        assert page.locator('[data-an-panel="post"]').is_visible()
        assert 'TG-B1' in page.locator('#an-results').inner_text()
        page.screenshot(path=str(output/'preview-post-desktop.png'), full_page=True)
        page.reload(wait_until='networkidle')
        assert page.locator('[data-an-panel="post"]').is_visible()
        page.set_viewport_size(dict(width=390, height=844))
        page.screenshot(path=str(output/'preview-post-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
        page.goto(base+'/admin/#anomaly', wait_until='networkidle')
        assert page.locator('[data-preview-view="anomaly"]').is_visible()
        assert '시안용 샘플 데이터' not in page.locator('body').inner_text()
        page.screenshot(path=str(output/'live-mobile.png'), full_page=True)
        for width in (1820, 1280, 960, 768, 390, 320):
            page.set_viewport_size(dict(width=width, height=1000 if width>600 else 844))
            page.goto(base+'/qa/', wait_until='networkidle')
            reference_badge=page.evaluate(badge_measure)
            for view in ('flow', 'network', 'anomaly'):
                page.goto(base+'/admin/preview#'+view, wait_until='networkidle')
                current_badge=page.evaluate(badge_measure)
                assert current_badge==reference_badge, f'Badge/home spacing mismatch at {width} {view}: {current_badge} vs {reference_badge}'
                assert page.locator(f'[data-preview-view="{view}"]').is_visible()
                assert page.locator(f'.mp-tab[href="#{view}"]').get_attribute('aria-current')=='page'
                assert page.locator('.top-bar .sample-status').is_visible(), f'Status hidden at {width}'
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Overflow {width} {view}'
                for item in page.locator('.top-bar .brand, .top-bar .admin-user, .top-bar .sample-status, .main-nav a').all():
                    box=item.bounding_box()
                    assert box and box['x']>=0, f'Header clipping {width} {view}'
                assert page.locator('.top-bar').evaluate('(e)=>Math.round(e.getBoundingClientRect().height)')==76
                assert page.locator('.brand-mark svg').evaluate('(e)=>e.getBoundingClientRect().width')==56
                if width in (1820,390):
                    page.screenshot(path=str(output/f'shared-{view}-{width}.png'), full_page=True)
        page.set_viewport_size(dict(width=390,height=844))
        page.goto(base+'/admin/db', wait_until='networkidle')
        assert page.locator('.top-bar .sample-status').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not errors, str(errors)
        print('BROWSER_CHECK_PASSED pre/post selection, reload, live/sample separation; shared shell 3 views x 6 widths and legacy page')
        print('Screenshots:', output)
        context.close()
        browser.close()


if __name__ == '__main__':
    run()
