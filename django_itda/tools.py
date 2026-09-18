"""도구 선언과 호출 경로 — 이 패키지의 가운데.

도구 하나는 **함수 하나**다. 시그니처(타입힌트·기본값)와 docstring 이 그대로
MCP 스키마가 된다 — 스키마를 손으로 쓰지 않고, 생성은 fastmcp 것을 쓴다
(`adapters/fastmcp.py`). 여기서 정하는 것은 스키마가 아니라 **호출 경로**다.

    권한 → 문맥 → 실행 → 매핑 → 기록

첫 인자는 언제나 `actor` 다. 자리 없이 부를 수 있는 도구를 만들지 않기 위해서고,
자리를 인자 목록의 맨 앞에 둔 것은 그것이 도구의 **첫 번째 질문**이기 때문이다 —
"무엇을 할 것인가" 보다 "누가 하는가" 가 먼저다. MCP 로 나갈 때 이 인자는
어댑터가 떼어 낸다. 모델은 자기 자리를 고를 수 없다.

실행을 **호출 문맥**으로 감싼다(`context.bind_call`). 도구 본문이 부르는 도메인
코드는 자기가 어느 문으로 불렸는지 인자로 받지 않고 `current_call()` 로 읽는다 —
그래야 도메인 장부의 `call_id` 가 이 호출의 궤적과 같은 값이 된다.

판정은 한 줄도 하지 않는다. 도구 본문이 도메인 서비스 함수를 부르고
`(판정, 결과 상태, 객체들)` 을 돌려주면, 이 모듈은 그것을 한 모양으로 옮기고
장부에 남긴다. 무엇이 허용되는지는 언제나 사용자의 도메인 코드가 답한다.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any

from django.db import OperationalError
from django.utils import timezone

from .busy import is_lock_failure
from .context import bind_call
from .models import ToolCall
from .results import ToolBusy, ToolDenied, tool_result
from .trajectory import elapsed_ms, new_call_id, record
from .verdict import Verdict


@dataclass(frozen=True)
class ToolSpec:
    """도구 하나의 선언. `Toolset.tool` 이 만든다."""

    name: str
    fn: Any
    perm: str | None = None
    query: bool = False
    handle_tool: str | None = None
    handle_subject: str = 'refund'
    forbidden_reason: str | None = None

    @property
    def description(self):
        return inspect.getdoc(self.fn) or ''

    @property
    def denied_reason(self):
        """권한 거부 문장. 선언이 안 주면 이름으로 만든다."""
        return self.forbidden_reason or f'{self.name} 을(를) 부를 권한이 없다'

    def signature_without_actor(self):
        """`actor` 를 뺀 시그니처 — 어댑터가 스키마를 만들 때 쓴다."""
        parameters = list(inspect.signature(self.fn).parameters.values())
        return inspect.Signature(parameters[1:])


@dataclass
class Toolset:
    """도구 묶음 하나 = 세계 하나의 문.

    `instructions` 는 MCP 서버의 안내문으로 그대로 나간다. 요약하지 말고
    원문을 실으라 — itda-django 는 AI 직원 시스템 프롬프트 전문을 싣는데,
    거기 남은 금지문과 실제 시행 코드를 대조하는 것이 8단계 부채 리포트의
    입력이기 때문이다.
    """

    name: str
    instructions: str = ''
    _specs: dict[str, ToolSpec] = field(default_factory=dict, repr=False)

    def tool(
        self,
        fn=None,
        *,
        name=None,
        perm=None,
        query=False,
        handle_tool=None,
        handle_subject='refund',
        forbidden_reason=None,
    ):
        """도구를 선언한다. 함수는 **그대로 돌려준다** — 직접 부를 수도 있어야 한다.

        - `perm` — `actor.has_perm()` 으로 묻는다. `None` 이면 검사하지 않는다.
        - `query` — 조회 도구. DENY 여도 오류를 던지지 않는다. 무엇이 나오든
          결과로 돌려준다. "지금 어떤 상태냐" 고 물었는데 거부된 건이라고
          오류를 던지면, 그건 판정이 아니라 **질문에 답을 안 한 것**이다.
        - `handle_tool` — ESCALATE 일 때 결과에 실을 승인 핸들의 조회 도구 이름.
        - `handle_subject` — 그 핸들의 `id`·`status` 를 어느 객체에서 읽을지.
        - `forbidden_reason` — 권한 거부 문장. 도메인의 말로 덮어쓴다.
        """

        def register(func):
            spec = ToolSpec(
                name=name or func.__name__,
                fn=func,
                perm=perm,
                query=query,
                handle_tool=handle_tool,
                handle_subject=handle_subject,
                forbidden_reason=forbidden_reason,
            )
            self._specs[spec.name] = spec
            return func

        return register(fn) if fn is not None else register

    def specs(self, actor=None, visible_only=False):
        """도구 목록.

        `visible_only` 는 **기본이 꺼짐**이다. 부를 수 없는 도구가 목록에
        보이는 것은 실수가 아니라 관찰 지점이다 — 목록에 있다는 것과 부를 수
        있다는 것은 다른 얘기이고, 그 차이 앞에서 모델이 무엇을 하는지 본다.
        가시성을 권한과 묶는 것은 별도의 결정이라 스위치로 남긴다.
        """
        specs = list(self._specs.values())
        if not visible_only:
            return specs
        return [
            spec
            for spec in specs
            if spec.perm is None or (actor is not None and actor.has_perm(spec.perm))
        ]

    def get(self, name):
        return self._specs[name]

    def call(self, name, actor, *, via=ToolCall.Via.MCP, **args):
        """도구를 부르고 **한 모양의 결과**를 돌려준다. 어느 갈래든 궤적 한 행을 남긴다.

        `via` 는 부르는 쪽이 준다 — 어댑터는 `mcp`, admin 액션은 `admin` 이다.
        (그래서 도구 인자에 `via` 라는 이름은 쓸 수 없다. 경로는 도구의 인자가
        아니라 호출의 성질이라서 여기 둔다.)

        예외 갈래가 셋이고, 셋은 서로 다른 사건이다.

        - `ToolDenied` — 자격이 없거나(권한) 세계가 거부했다(판정).
        - `ToolBusy` — 잠겨서 판정하지 못했다. 다시 보내면 될 요청이다.
        - 그 밖의 예외 — **그대로 올린다.** 500 은 500 이다. 모르는 고장을
          "잠시 후 다시" 로 번역하면 클라이언트는 영원히 다시 보낸다.

        셋은 **어디서 판정됐든 같은 이름으로** 남는다. 여기 `spec.perm` 검사에서
        막힌 거부와 도구 본문이 올린 거부는 같은 사건이므로 궤적에서도 같은
        갈래여야 한다 — 한쪽만 `exception` 으로 남으면 감사 화면에서 거부가
        500 급 고장과 섞인다. 본문이 올린 것은 기록만 하고 **그대로 다시
        올린다**(문장·traceback·예외 종류를 바꾸지 않는다).
        """
        spec = self._specs[name]
        call_id = new_call_id()
        started_at = timezone.now()
        base = {
            'call_id': call_id,
            'tool': name,
            'actor': actor if getattr(actor, 'pk', None) else None,
            'via': via,
            'arguments': args,
            'started_at': started_at,
        }

        if spec.perm and not (actor is not None and actor.has_perm(spec.perm)):
            denied = ToolDenied.forbidden(spec.denied_reason)
            record(
                **base,
                error=ToolCall.Error.FORBIDDEN,
                reason=str(denied),
                duration_ms=elapsed_ms(started_at),
            )
            raise denied

        try:
            # 도구 본문이 부르는 서비스·모델이 장부를 적으면 **이 호출 ID** 가
            # 실린다. 도구면 궤적(`ToolCall`)과 도메인 장부가 잇닿는 지점이고,
            # 자리를 같이 묶는 것은 문맥을 읽는 쪽이 도구의 인자 목록을
            # 다시 뒤지지 않게 하기 위해서다.
            with bind_call(call_id, via, actor):
                returned = spec.fn(actor, **args)
        except ToolDenied as denied:
            # 본문이 올린 거부. 거부는 고장이 아니다 — 권한 검사에서 막힌 것과
            # 같은 갈래로 남긴다. `query` 여부로 갈라지지 않는다: 조회 도구의
            # DENY **반환**은 결과로 주는 것이 계약이지만, 본문이 예외를
            # **올린 것**은 도구가 명시적으로 거부한 것이라 결과로 바꾸지 않는다.
            record(
                **base,
                **self._denied_fields(denied),
                duration_ms=elapsed_ms(started_at),
            )
            raise
        except ToolBusy as busy:
            # 본문이 직접 올린 잠금 실패. 아래 `OperationalError` 번역 갈래와
            # 같은 사건이므로 같은 이름으로 남는다.
            record(
                **base,
                error=ToolCall.Error.BUSY,
                reason=str(busy),
                duration_ms=elapsed_ms(started_at),
            )
            raise
        except OperationalError as failure:
            if not is_lock_failure(failure):
                self._record_exception(base, started_at, failure)
                raise
            busy = ToolBusy()
            record(
                **base,
                error=ToolCall.Error.BUSY,
                reason=str(busy),
                duration_ms=elapsed_ms(started_at),
            )
            raise busy from failure
        except Exception as failure:
            self._record_exception(base, started_at, failure)
            raise

        if isinstance(returned, dict):
            # 판정 없는 순수 조회 — 물었을 뿐 세계에 아무것도 시키지 않았다.
            # `kind`·`outcome` 을 지어내지 않는다.
            record(**base, duration_ms=elapsed_ms(started_at))
            return {'call_id': call_id, **returned}

        verdict, outcome_state, objects = returned
        result = tool_result(
            verdict,
            outcome_state,
            call_id=call_id,
            handle=self._handle(spec, verdict, objects),
            **objects,
        )

        if verdict.kind == Verdict.DENY and not spec.query:
            denied = ToolDenied(verdict)
            record(
                **base,
                kind=verdict.kind,
                outcome=outcome_state,
                rule_ids=list(verdict.rule_ids),
                reason=verdict.reason,
                error=ToolCall.Error.DENIED,
                duration_ms=elapsed_ms(started_at),
            )
            raise denied

        record(
            **base,
            kind=verdict.kind,
            outcome=outcome_state,
            rule_ids=list(verdict.rule_ids),
            reason=verdict.reason,
            duration_ms=elapsed_ms(started_at),
        )
        return result

    def _handle(self, spec, verdict, objects):
        """승인 핸들 — ESCALATE 를 **기다릴 수 있는 상태**로 만드는 최소치.

        푸시는 없다. 그래서 격상된 쪽에 "무엇을 어떻게 물어보면 되는지" 를
        같이 준다: 조회 도구 이름과 대상 ID 다. MCP 스펙의 Tasks 를 쓰지 않는
        폴백 관행이고, 첫 사용자가 실접속에서 이 폴링으로 충분했다.

        도구가 `objects['handle']` 로 **필드를 보탤 수 있다.** 기다리는 방법이
        폴링뿐이 아니기 때문이다 — 사람이 여는 URL 이 그 첫 사례다(itda-django
        6단계의 결제 링크: `url`·`expires_at`). 보탠 것이 자동 핸들 위에 얹히므로
        도구는 `check_tool` 도 덮어쓸 수 있다.

        `pop` 인 이유 — objects 에 남으면 결과의 최상위 `handle` 을 나중에
        덮어쓴다(`tool_result` 가 objects 를 마지막에 펼친다). 격상이 아니면
        꺼내서 **버린다**. 핸들은 격상에만 있고, 기다릴 것이 없는 답에 기다리는
        방법을 실어 보내면 그건 없는 길을 알려 주는 것이다.
        """
        extra = objects.pop('handle', None)
        if verdict.kind != Verdict.ESCALATE:
            return None
        if not spec.handle_tool and not extra:
            return None
        subject = objects.get(spec.handle_subject) or {}
        handle = {
            'check_tool': spec.handle_tool,
            'id': subject.get('id'),
            'status': subject.get('status'),
        }
        handle.update(extra or {})
        return handle

    def _denied_fields(self, denied):
        """본문이 올린 거부를 궤적 필드로 옮긴다.

        `verdict` 가 없으면 `ToolDenied.forbidden()` 이다 — 판정에 닿은 적이
        없으니 `kind` 도 `rule_ids` 도 적지 않는다. 판정을 들고 왔으면 DENY
        반환 갈래와 **같은 모양**으로 남긴다.

        `outcome` 은 어느 쪽이든 비운다. 결과 상태는 도구가 판정과 **함께
        돌려주는** 것이라 예외에는 실려 오지 않는다 — 지어내면 장부가 거짓말한다.

        `verdict` 는 덕 타이핑(`VerdictLike`)이라 이 패키지의 `Verdict` 가
        아닐 수 있다. 그래서 필드를 방어적으로 읽는다.
        """
        verdict = denied.verdict
        if verdict is None:
            return {'error': ToolCall.Error.FORBIDDEN, 'reason': str(denied)}
        return {
            'error': ToolCall.Error.DENIED,
            'kind': getattr(verdict, 'kind', ''),
            'rule_ids': list(getattr(verdict, 'rule_ids', None) or []),
            'reason': getattr(verdict, 'reason', ''),
        }

    def _record_exception(self, base, started_at, failure):
        """세계가 터졌다. 판정 어휘를 적지 않는다 — 판정에 닿지 못했다."""
        record(
            **base,
            error=ToolCall.Error.EXCEPTION,
            reason=f'{type(failure).__name__}: {failure}',
            duration_ms=elapsed_ms(started_at),
        )
