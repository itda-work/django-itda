"""fastmcp 어댑터 — 도구 선언을 MCP 서버로 옮긴다.

**이 패키지에서 `fastmcp` 를 임포트하는 유일한 모듈이다.** 판정·궤적·승인
핸들은 전송을 모르고, 여기는 도메인을 모른다. 나중에 다른 MCP 구현으로
갈아타도 갈리는 것은 이 파일 하나다.

하는 일은 둘뿐이다.

1. **자리를 숨긴다.** 도구 함수의 첫 인자 `actor` 를 시그니처에서 떼어 낸다.
   모델은 자기 자리를 고를 수 없어야 한다 — 자리는 프로세스를 띄운 열쇠가
   정한다(`actor_provider`). 스키마는 남은 인자들로 fastmcp 가 만든다.
2. **예외를 옮긴다.** `ToolDenied`·`ToolBusy` 를 `ToolError` 로. 문장은 그대로
   싣는다 — 규칙 ID 와 대안이 들어 있어야 모델이 스스로 고친다.

`ToolError` 로 옮기지 않는 예외는 그대로 올라간다. 모르는 고장을 예쁜 문장으로
감싸면 고장이 있었다는 사실이 사라진다.
"""

import functools

from asgiref.sync import sync_to_async

try:
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError
except ImportError as missing:  # pragma: no cover - 설치 안내
    raise ImportError(
        'fastmcp 가 없다 — django-itda 의 MCP 어댑터는 선택 의존이다. '
        'uv sync --extra fastmcp 로 설치하라.'
    ) from missing

from ..models import ToolCall
from ..results import ToolBusy, ToolDenied


def build_server(toolset, actor_provider, *, visible_only=False):
    """`Toolset` 을 `FastMCP` 서버로. 등록만 하고 실행은 부르는 쪽이 한다."""
    server = FastMCP(toolset.name, instructions=toolset.instructions or None)
    for spec in toolset.specs(actor=actor_provider(), visible_only=visible_only):
        server.tool(_wrap(toolset, spec, actor_provider), name=spec.name)
    return server


def _wrap(toolset, spec, actor_provider):
    """`actor` 없는 얼굴을 씌운다. 스키마는 이 얼굴에서 나온다.

    래퍼가 `async` 인 것은 MCP 서버가 async 로 돌기 때문이고, 본문을
    `sync_to_async(thread_sensitive=True)` 로 넘기는 것은 도구 본문이 Django ORM 을
    그대로 부르는 **동기 코드**이기 때문이다. Django 는 async 문맥의 ORM 호출을
    거부한다(`SynchronousOnlyOperation`). `thread_sensitive` 를 켜면 모든 호출이
    **같은 실행 스레드**로 모여 DB 커넥션이 스레드 수만큼 늘어나지 않는다.
    """

    @functools.wraps(spec.fn)
    async def call(**kwargs):
        try:
            return await sync_to_async(toolset.call, thread_sensitive=True)(
                spec.name, actor_provider(), via=ToolCall.Via.MCP, **kwargs
            )
        except (ToolDenied, ToolBusy) as refused:
            raise ToolError(str(refused)) from None

    signature = spec.signature_without_actor()
    call.__signature__ = signature
    call.__name__ = spec.name
    call.__doc__ = spec.description
    # `functools.wraps` 가 실어 온 원 함수의 어노테이션에는 `actor` 가 남아 있다.
    # 스키마 생성이 그것을 보고 자리를 인자로 만들어 버리면 안 된다.
    call.__annotations__ = {
        name: parameter.annotation
        for name, parameter in signature.parameters.items()
        if parameter.annotation is not parameter.empty
    }
    return call
