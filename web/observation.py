"""업무 동작을 바꾸지 않는 요청 관측 문맥. 요청 본문·인증값은 복제하지 않는다."""
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

context = ContextVar('ticketing_observation', default=None)
ACTIONS = {'login', 'start', 'verify', 'confirm', 'cancel', 'heartbeat'}


def begin(request, data):
    from .services import repo
    from .events import emit

    action = data.get('action')
    metadata = {'observationRequestId': str(uuid4()),
                'observationStartedAt': datetime.now(timezone.utc).isoformat()}
    token = context.set(metadata)
    if isinstance(action, str) and action in ACTIONS:
        user = request.session.get('user')
        # 요청이 주장한 공연·시도 대신 서버가 가진 동일 계정의 시도만 연결한다.
        try:
            with repo.lock:
                attempt = repo.attempts.get(user)
                if not attempt or attempt['id'] != data.get('attemptId'):
                    attempt = None
                if attempt is not None:
                    metadata['observationStage'] = attempt['stage']
                emit(user, 'ticketing_action_received', attempt,
                     occurredAt=metadata['observationStartedAt'],
                     details={'action': action, 'contextKnown': attempt is not None})
        except Exception:
            # 관측 실패가 원래 업무 응답을 대체하지 않게 한다.
            pass
    return token
