"""세계가 잠겨서 **판정하지 못한** 것은 판정이 아니다 (5단계).

409 는 "자격은 있지만 세계가 지금 그 상태가 아니다"라는 **판정**이다.
`database is locked` 는 판정이 아니라 **판정에 실패한 것**이다. 둘을 섞으면
세계가 거짓말을 한다 — 다시 보내면 될 요청에 대고 규칙 위반이라고 답하면,
AI 직원은 있지도 않은 규칙을 고객에게 설명한다.

그렇다고 500 으로 두어서도 안 된다. 500 은 "우리가 깨졌다"이고, 받은 쪽이
재시도해도 되는지 알 수 없다. 잠금 실패의 정직한 이름은 **503 + Retry-After** 다 —
"지금은 못 한다, 잠시 후 같은 요청을 다시 보내라."
"""

from django.db import OperationalError
from django.http import HttpResponse, JsonResponse

BUSY_REASON = (
    '세계가 잠겨 있어 판정하지 못했다 — 거부(409)가 아니다. '
    '잠시 후 같은 요청을 다시 보내라.'
)
RETRY_AFTER = 1


class WorldBusyMiddleware:
    """잠금 실패만 503 으로 옮긴다. **그 외 예외는 건드리지 않는다** — 500 은 500이다."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, OperationalError):
            return None
        text = str(exception).lower()
        if 'locked' not in text and 'busy' not in text:
            return None

        if 'text/html' in request.headers.get('Accept', ''):
            response = HttpResponse(
                BUSY_REASON, status=503, content_type='text/plain; charset=utf-8'
            )
        else:
            response = JsonResponse(
                {'error': 'busy', 'reason': BUSY_REASON, 'retry_after': RETRY_AFTER},
                status=503,
                json_dumps_params={'ensure_ascii': False},
            )
        # 판정 필드(`kind`·`rule_ids`)를 싣지 않는다. 판정한 적이 없기 때문이다.
        response['Retry-After'] = str(RETRY_AFTER)
        return response
