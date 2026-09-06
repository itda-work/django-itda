"""세계가 잠겨서 **판정하지 못한** 것은 판정이 아니다 (5단계).

409 는 "자격은 있지만 세계가 지금 그 상태가 아니다"라는 **판정**이다.
`database is locked` 는 판정이 아니라 **판정에 실패한 것**이다. 둘을 섞으면
세계가 거짓말을 한다 — 다시 보내면 될 요청에 대고 규칙 위반이라고 답하면,
AI 직원은 있지도 않은 규칙을 고객에게 설명한다.

그렇다고 500 으로 두어서도 안 된다. 500 은 "우리가 깨졌다"이고, 받은 쪽이
재시도해도 되는지 알 수 없다. 잠금 실패의 정직한 이름은 **503 + Retry-After** 다 —
"지금은 못 한다, 잠시 후 같은 요청을 다시 보내라."

## 무엇이 잠금 실패인가 — 문자열로 세지 않는다

`OperationalError` 는 SQLite 가 내는 온갖 것을 다 담는다. `no such table:
busy_orders` 도 `OperationalError` 이고, 메시지에 `busy` 가 들어 있다.
그걸 잠금으로 세면 **스키마 오류를 "잠시 후 다시 보내라"로 번역**하는 것이고,
클라이언트는 영원히 다시 보낸다.

그래서 판별은 두 단계다.

1. 원인 예외(`__cause__`)의 `sqlite_errorcode` 가 `SQLITE_BUSY`·`SQLITE_LOCKED` 인가.
   확장 오류 코드는 하위 8비트에 기본 코드를 담으므로 `& 0xFF` 로 벗긴다.
2. 코드가 없으면(다른 백엔드·주입 테스트) 알려진 **정확한 메시지**와 일치하는가.
   부분 문자열이 아니라 전체 일치다.
"""

import sqlite3

from django.db import OperationalError
from django.http import HttpResponse, JsonResponse

BUSY_REASON = (
    '세계가 잠겨 있어 이 요청은 판정하지 못했다 — 거부(409)가 아니다. '
    '잠시 후 같은 요청을 다시 보내라.'
)
RETRY_AFTER = 1

# SQLite 가 "지금은 못 준다"고 말하는 두 코드.
BUSY_CODES = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)

# 오류 코드를 못 얻었을 때만 쓰는 대조표. 부분 일치가 아니라 전체 일치다.
LOCK_MESSAGES = ('database is locked', 'database table is locked')


def is_lock_failure(exception):
    """잠금 실패인가. `OperationalError` 라는 것만으로는 답이 안 된다."""
    if not isinstance(exception, OperationalError):
        return False
    code = getattr(exception.__cause__, 'sqlite_errorcode', None)
    if code is not None:
        return code & 0xFF in BUSY_CODES
    return str(exception).strip().lower() in LOCK_MESSAGES


class WorldBusyMiddleware:
    """잠금 실패만 503 으로 옮긴다. **그 외 예외는 건드리지 않는다** — 500 은 500이다.

    `process_exception` 은 뷰에서 올라온 예외를 본다. 이 아래(안쪽) 미들웨어가
    던진 예외까지 전부 잡는 최외곽 catch 가 아니다 — 자리는 그만큼만 한다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not is_lock_failure(exception):
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
        # 문장이 "이 요청은"으로 한정된 것도 의도다 — admin 다건 액션처럼 앞 건이
        # 이미 커밋됐을 수 있는 경우에 "아무 일도 안 일어났다"로 읽히면 안 된다.
        response['Retry-After'] = str(RETRY_AFTER)
        return response
