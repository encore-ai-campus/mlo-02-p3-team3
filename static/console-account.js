/* 관리자 콘솔 공통 로그아웃 · 기존 Django 세션과 CSRF 검증 사용. */
(() => {
  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-console-logout]');
    if (!button || button.disabled) return;
    button.disabled = true;
    const label = button.textContent;
    button.textContent = '로그아웃 중…';
    try {
      const csrf = () => document.cookie.split('; ').find(value => value.startsWith('csrftoken='))?.slice('csrftoken='.length);
      if (!csrf()) await fetch('/login/', {credentials: 'same-origin', cache: 'no-store'});
      const response = await fetch('/api/action/', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent(csrf() || '')},
        body: JSON.stringify({action: 'logout'}),
      });
      if (!response.ok && response.status !== 401) throw new Error('Logout failed');
      try {
        sessionStorage.removeItem('tg-active');
        sessionStorage.removeItem('tg-events');
      } catch (_) { /* 저장소 사용 불가 시에도 세션 종료는 완료한다. */ }
      window.location.assign('/login/');
    } catch (_) {
      button.disabled = false;
      button.textContent = label;
      window.alert('로그아웃에 실패했습니다. 다시 시도해 주세요.');
    }
  });
})();
