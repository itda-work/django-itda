"""궤적 기록 — `create()` 한 줄. 그 한 줄을 부르는 자리가 어디인지가 요점이다.

`Toolset.call` 은 갈래마다 **명시적으로** 이것을 부른다. `finally` 한 곳에서
모아 찍지 않는다 — 읽는 사람이 "이 갈래는 무엇을 기록하는가" 를 그 자리에서
보게 하려는 것이고, 갈래마다 남길 것이 실제로 다르기 때문이기도 하다.

트랜잭션 밖에서 부른다. 도구 실행이 롤백돼도 **불린 사실은 남아야 한다** —
세계가 안 움직인 것과 아무도 부르지 않은 것은 다른 사건이다.
"""

import uuid

from django.utils import timezone

from .models import ToolCall


def new_call_id():
    """호출 상관 ID. 결과의 `call_id` 와 궤적 행이 이것으로 이어진다."""
    return uuid.uuid4().hex


def elapsed_ms(started_at):
    return int((timezone.now() - started_at).total_seconds() * 1000)


def record(**fields):
    """궤적 한 행을 남긴다."""
    return ToolCall.objects.create(**fields)
