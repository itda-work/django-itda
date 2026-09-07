"""요청 하나 = 호출 하나 — `CallContextMiddleware`.

미들웨어를 **직접 부른다.** URL 도 뷰도 없는 패키지 테스트 설정 그대로에서
서야 한다(`tests/settings.py` — 도메인 앱도 미들웨어도 없다).
"""

import re

import pytest
from django.test import RequestFactory, override_settings

from django_itda.context import current_call
from django_itda.middleware import CallContextMiddleware

VIA = {'/api/': 'api', '/admin/': 'admin', '/agent/': 'console', '/pay/': 'customer'}


@pytest.fixture
def factory():
    return RequestFactory()


def run(request):
    """미들웨어를 통과시키고 `(응답, 안에서 본 문맥)` 을 돌려준다."""
    seen = {}

    def view(_request):
        from django.http import HttpResponse

        seen['context'] = current_call()
        return HttpResponse('ok')

    response = CallContextMiddleware(view)(request)
    return response, seen['context']


@override_settings(ITDA={'VIA': VIA})
def test_헤더가_없으면_새로_만든다(factory):
    response, context = run(factory.get('/agent/'))

    assert re.fullmatch(r'[0-9a-f]{32}', context.call_id)
    assert response['X-Call-ID'] == context.call_id


@override_settings(ITDA={'VIA': VIA})
def test_헤더의_상관_ID_를_그대로_싣는다(factory):
    given = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'

    response, context = run(factory.get('/api/orders/', HTTP_X_REQUEST_ID=given))

    assert context.call_id == given
    assert response['X-Call-ID'] == given


@override_settings(ITDA={'VIA': VIA})
def test_uuid_표기의_하이픈은_벗긴다(factory):
    _response, context = run(
        factory.get('/api/', HTTP_X_REQUEST_ID='A1B2C3D4-E5F6-A7B8-C9D0-E1F2A3B4C5D6')
    )

    assert context.call_id == 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'


@override_settings(ITDA={'VIA': VIA})
@pytest.mark.parametrize(
    '주어진_값',
    ['', '   ', 'not-hex-값', 'z' * 32, '0' * 33, '<script>'],
)
def test_형식이_아니면_무시하고_새로_만든다(factory, 주어진_값):
    """믿되 형식은 검사한다. 아무 문자열이나 장부의 상관 ID 가 되면 안 된다."""
    _response, context = run(factory.get('/api/', HTTP_X_REQUEST_ID=주어진_값))

    assert re.fullmatch(r'[0-9a-f]{32}', context.call_id)
    assert context.call_id != 주어진_값


@override_settings(ITDA={'VIA': VIA})
@pytest.mark.parametrize(
    ('path', 'via'),
    [
        ('/api/orders/', 'api'),
        ('/admin/orders/order/', 'admin'),
        ('/agent/', 'console'),
        ('/pay/12/토큰/', 'customer'),
        ('/', 'web'),
        ('/무엇이든/', 'web'),
    ],
)
def test_접두사_표가_문_이름을_고른다(factory, path, via):
    _response, context = run(factory.get(path))

    assert context.via == via


def test_설정이_없으면_전부_web_이다(factory):
    """표는 세계의 것이다 — 없으면 패키지가 지어내지 않는다."""
    _response, context = run(factory.get('/api/'))

    assert context.via == 'web'


@override_settings(ITDA={'VIA': VIA})
def test_요청이_끝나면_문맥은_풀린다(factory):
    run(factory.get('/api/'))

    assert current_call().call_id == ''
    assert current_call().via == 'shell'


@override_settings(ITDA={'VIA': VIA})
def test_actor_는_묶지_않는다(factory):
    """토큰 인증은 뷰 안에서 일어난다 — 미들웨어 시점엔 자리를 모른다."""
    _response, context = run(factory.get('/api/'))

    assert context.actor is None
