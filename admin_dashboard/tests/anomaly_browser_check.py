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
        page.goto(base+'/admin/preview#anomaly', wait_until='networkidle')
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
        assert not errors, str(errors)
        print('BROWSER_CHECK_PASSED pre/post selection, reload, mobile, live/sample separation')
        print('Screenshots:', output)
        context.close()
        browser.close()


if __name__ == '__main__':
    run()
