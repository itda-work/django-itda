"""호출 문맥 — 묶은 것만 보이고, 나가면 되돌아온다.

이 파일이 지키는 것은 셋이다. **기본값이 shell 인가**, **중첩에서 안쪽이
이기고 나갈 때 바깥이 되살아나는가**, 그리고 **`sync_to_async` 경로에서도
문맥이 따라가는가.** 셋째가 이 모듈의 존재 이유다 — MCP 어댑터는 도구 본문을
`sync_to_async(thread_sensitive=True)` 로 다른 스레드에 넘긴다. 스레드 로컬이면
거기서 문맥이 끊기고, 장부의 `call_id` 가 빈 값이 된다.
"""

import asyncio

from asgiref.sync import sync_to_async

from django_itda.context import CallContext, bind_call, current_call


def test_묶은_것이_없으면_shell_이다():
    context = current_call()

    assert context == CallContext()
    assert (context.call_id, context.via, context.actor) == ('', 'shell', None)


def test_묶으면_보이고_나가면_사라진다():
    with bind_call('abc123', 'mcp', actor='직원') as bound:
        assert bound == current_call()
        assert current_call().call_id == 'abc123'
        assert current_call().via == 'mcp'
        assert current_call().actor == '직원'

    assert current_call() == CallContext()


def test_중첩은_안쪽이_이기고_바깥이_되살아난다():
    with bind_call('바깥', 'api'):
        with bind_call('안쪽', 'admin'):
            assert current_call().call_id == '안쪽'
            assert current_call().via == 'admin'
        assert current_call().call_id == '바깥'
        assert current_call().via == 'api'

    assert current_call().via == 'shell'


def test_None_호출_ID_는_빈_문자열이다():
    """장부의 `call_id` 는 `CharField(blank)` 다 — `None` 을 흘려보내지 않는다."""
    with bind_call(None, 'shell'):
        assert current_call().call_id == ''


def test_sync_to_async_경로에서도_문맥이_따라간다():
    """어댑터가 도구 본문을 넘기는 그 경로다(`adapters/fastmcp.py`).

    `contextvars` 는 컨텍스트를 복사해 실행 스레드로 넘긴다. 스레드 로컬이면
    여기서 끊긴다.
    """
    seen = {}

    def 본문():
        seen['안'] = current_call()

    async def 도구():
        with bind_call('deadbeef', 'mcp', actor='직원'):
            await sync_to_async(본문, thread_sensitive=True)()

    asyncio.run(도구())

    assert seen['안'].call_id == 'deadbeef'
    assert seen['안'].via == 'mcp'
    assert current_call() == CallContext(), '비동기 호출이 바깥 문맥을 더럽히지 않는다.'


def test_스레드_안에서_묶은_것은_밖으로_새지_않는다():
    """다른 스레드는 자기 문맥을 갖는다 — 요청 둘이 서로의 ID 를 보면 안 된다."""
    import threading

    seen = {}

    def 다른_요청():
        seen['시작'] = current_call().call_id
        with bind_call('스레드', 'api'):
            seen['안'] = current_call().call_id

    with bind_call('메인', 'console'):
        thread = threading.Thread(target=다른_요청)
        thread.start()
        thread.join()
        assert current_call().call_id == '메인'

    assert seen == {'시작': '', '안': '스레드'}
