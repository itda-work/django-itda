"""호출 문맥 — **어느 문으로 들어온 요청이 지금 세계를 움직이는가.**

도메인 코드는 요청도 도구도 모른다. `Order.mark_paid` 는 자기가 MCP 도구에서
불렸는지 결제 페이지에서 불렸는지 알 방법이 없고, 알아서도 안 된다 — 그것을
인자로 받기 시작하면 모든 전이 메서드의 시그니처에 전송 계층이 스며든다.

그래서 문맥을 **문이 묶는다.** 도구면(`Toolset.call`)과 미들웨어
(`CallContextMiddleware`)가 요청 하나의 수명 동안 `(call_id, via, actor)` 를
`contextvars` 에 걸어 두고, 장부를 적는 쪽은 `current_call()` 로 읽기만 한다.

`contextvars` 인 이유는 스레드 로컬과 달리 **async 문맥까지 따라가기** 때문이다
(`asgiref.sync_to_async` 가 컨텍스트를 복사해 실행 스레드로 넘긴다 —
`adapters/fastmcp.py` 의 도구 래퍼가 그 경로다).

묶인 것이 없으면 `CallContext('', 'shell', None)` 이다. **shell 이 기본인 것이
정직한 기본값**이다 — 아무 문도 열리지 않았는데 돌고 있는 코드는 사람이
직접 부른 것(shell·management command·테스트)이기 때문이다.
"""

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

SHELL = 'shell'


@dataclass(frozen=True)
class CallContext:
    """지금 도는 호출 하나. 값이지 상태가 아니다 — 바꾸려면 다시 묶는다."""

    call_id: str = ''
    via: str = SHELL
    actor: Any = None


#: 기본값을 `None` 으로 두고 `current_call()` 이 채운다. `ContextVar` 의 기본값
#: 자리에 객체를 두면 모듈이 임포트되는 순간 하나가 만들어져 프로세스 전체가
#: 공유한다 — frozen 이라 사고는 안 나지만, 공유하지 않는 편이 정직하다.
_current = contextvars.ContextVar('django_itda_call', default=None)

#: 아무 문도 열리지 않았을 때의 답.
UNBOUND = CallContext()


def current_call():
    """지금 묶여 있는 호출 문맥. 없으면 shell 기본값."""
    return _current.get() or UNBOUND


@contextmanager
def bind_call(call_id, via, actor=None):
    """이 블록 안에서 도는 코드에 호출 문맥을 묶는다.

    중첩되면 안쪽이 이긴다. 나갈 때 **토큰으로 되돌리므로**(`reset`) 바깥
    문맥이 되살아난다 — 문 안에서 다른 문을 여는 경우(admin 액션이 도구를
    부르는 경우)에도 바깥이 오염되지 않는다.
    """
    token = _current.set(CallContext(call_id=call_id or '', via=via, actor=actor))
    try:
        yield _current.get()
    finally:
        _current.reset(token)
