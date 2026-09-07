"""요청 하나 = 호출 하나. 그 사실을 문맥에 묶는 미들웨어.

`Toolset.call` 이 MCP 문에서 하는 일을 HTTP 문에서 한다. 이것이 꽂혀 있으면
콘솔·admin·API·고객 페이지에서 일어난 전이도 **어느 문으로 들어왔는지** 를
아는 채로 장부에 적힌다(발견 3 — admin 액션이 아무 데도 안 남던 그 자리다).

## `actor` 는 묶지 않는다

토큰 인증이 뷰 데코레이터 안에서 일어나므로 미들웨어 시점에는 아직 자리를
모른다(`request.user` 는 세션 문에서만 채워지고, 그마저 `AuthenticationMiddleware`
아래에서다). 반쯤 아는 것을 묶으면 장부가 문마다 다른 정확도를 갖는다 —
그래서 **자리는 문맥이 아니라 명시 인자**다(`Event.record(actor=...)`).

## `X-Request-ID` 를 믿는다 — 형식만 검사한다

클라이언트가 남의 ID 를 붙여 보낼 수 있다. 그것을 막는 것은 이 층의 일이
아니고(8단계 소재), 여기서 하는 것은 **형식 검사뿐**이다 — 하이픈을 뺀 16진
32자 이내여야 한다. 아니면 무시하고 새로 만든다. 되돌려 주는 `X-Call-ID`
응답 헤더는 관찰용이다: 화면에서 본 것을 `just ledger --call <id>` 로 잇는다.
"""

import re
import uuid

from django.conf import settings

from .context import bind_call

#: 헤더로 받은 상관 ID 의 형식. 하이픈은 벗겨서 센다(uuid 표기 그대로 와도 받는다).
CALL_ID_PATTERN = re.compile(r'^[0-9a-fA-F]{1,32}$')

#: 접두사 표에 없는 경로의 기본 문. 'shell' 이 아니다 — 요청은 왔다.
DEFAULT_VIA = 'web'

REQUEST_ID_HEADER = 'HTTP_X_REQUEST_ID'
CALL_ID_HEADER = 'X-Call-ID'


class CallContextMiddleware:
    """요청 수명 동안 `(call_id, via)` 를 묶는다. 그 밖에는 아무것도 하지 않는다."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        call_id = self.call_id(request)
        with bind_call(call_id, self.via(request.path)):
            response = self.get_response(request)
        response[CALL_ID_HEADER] = call_id
        return response

    @staticmethod
    def call_id(request):
        given = (request.META.get(REQUEST_ID_HEADER) or '').strip().replace('-', '')
        if CALL_ID_PATTERN.match(given):
            return given.lower()
        return uuid.uuid4().hex

    @staticmethod
    def via(path):
        """경로 접두사 표에서 문 이름을 고른다 — **첫 일치**가 이긴다.

        표는 세계의 것이다(`settings.ITDA['VIA']`). 패키지가 `/admin/` 이나
        `/pay/` 같은 남의 URL 을 알고 있을 이유가 없다.
        """
        table = (getattr(settings, 'ITDA', None) or {}).get('VIA') or {}
        for prefix, via in table.items():
            if path.startswith(prefix):
                return via
        return DEFAULT_VIA
