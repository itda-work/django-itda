"""토큰 인증 — 브라우저 밖에서 들어오는 요청에 자리를 준다.

`token_required` 가 하는 일은 딱 하나다. `Authorization: Bearer <키>` 를 읽어
`request.user` 를 채운다. 권한 검사는 하지 않는다 — 그건 뷰가 `has_perm` 으로
따로 묻는다. **인증(누구인가)과 인가(무엇을 할 수 있는가)는 다른 질문**이고,
이 세계에서 그 둘을 섞지 않는 것이 1단계에서 본 법이다.

CSRF 면제는 여기서 함께 건다. CSRF 토큰은 브라우저 세션을 노린 위조를 막는
장치인데, 이 경로는 세션 쿠키를 아예 쓰지 않으므로 막을 위조가 없다.
"""

import functools

from django.http import JsonResponse

from .models import APIToken


def json_error(status, error, reason):
    return JsonResponse(
        {'error': error, 'reason': reason},
        status=status,
        json_dumps_params={'ensure_ascii': False},
    )


def token_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get('Authorization', '')
        scheme, _, raw = header.partition(' ')
        if scheme.lower() != 'bearer' or not raw.strip():
            return json_error(
                401,
                'unauthorized',
                'Authorization: Bearer <토큰> 헤더가 필요합니다. '
                '토큰은 manage.py issue_token 으로 발급합니다.',
            )
        user = APIToken.authenticate(raw.strip())
        if user is None:
            return json_error(401, 'unauthorized', '알 수 없는 토큰입니다.')
        request.user = user
        return view(request, *args, **kwargs)

    wrapper.csrf_exempt = True
    return wrapper
