"""사이트 전체 오류 화면 — 403 을 두 자리로 나눈다.

`templates/agent/403.html` 의 "세계가 거부했다" 는 AI 직원 콘솔의 1·2단계 교육
장치이고, MCP 도구 오류와 같은 목소리다 — AI 가 읽는 자리(콘솔·그 콘솔이 두드리는
`/orders/` 확정 엔드포인트)에서만 쓴다. admin 을 포함해 사람이 보는 일반 웹
페이지는 교육 문구 없는 평범한 403(`templates/403.html`)을 본다.
"""

from django.shortcuts import render

# 세계의 목소리로 답할 경로. `/agent/` 는 콘솔, `/orders/` 는 콘솔 버튼이 두드리는
# 확정 엔드포인트뿐이다(사람 전용 페이지가 없다).
#
# 6단계에서 사람의 문이 둘 생겼지만 목록은 그대로다 — `/pay/`(고객의 결제 페이지)와
# `/accounts/`(고객 로그인)는 **사람이 보는 페이지**라 일반 403 이 맞다. 결제 페이지를
# `/orders/` 아래에 두지 않은 이유이기도 하다(`config/urls.py`).
AGENT_PREFIXES = ('/agent/', '/orders/')


def permission_denied(request, exception, template_name='403.html'):
    """Django 기본 403 핸들러 자리. 경로에 따라 템플릿만 갈아 끼운다."""
    if request.path.startswith(AGENT_PREFIXES):
        template_name = 'agent/403.html'
    return render(request, template_name, status=403)
