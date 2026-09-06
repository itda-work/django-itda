"""fastmcp 어댑터 — 자리를 숨기고 스키마를 남의 것으로 만든다.

`fastmcp` 는 선택 의존이라 없으면 건너뛴다. 여기서 재는 것은 스키마 생성이
아니라(그건 fastmcp 의 몫이다) **우리가 넘긴 얼굴이 그대로 스키마가 되는가** 다.
"""

import asyncio

import pytest

pytest.importorskip('fastmcp')

from django.contrib.auth.models import User  # noqa: E402

from django_itda.adapters.fastmcp import build_server  # noqa: E402
from django_itda.tools import Toolset  # noqa: E402
from django_itda.verdict import Outcome, Verdict  # noqa: E402


@pytest.fixture
def world():
    toolset = Toolset(name='더미-세계', instructions='안내문')

    @toolset.tool
    def propose_thing(actor, amount: int | None = None, note: str = '메모'):
        """무언가를 제안한다."""
        return Verdict(kind=Verdict.ALLOW), Outcome.COMMITTED, {'thing': {'id': 1}}

    @toolset.tool(query=True)
    def list_things(actor):
        """목록을 읽는다."""
        return {'things': []}

    return toolset


@pytest.fixture
def server(world, db):
    자리 = User.objects.create_user('직원')
    return build_server(world, lambda: 자리)


def tools_of(server):
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_선언한_도구가_그대로_노출된다(server, world):
    assert set(tools_of(server)) == {spec.name for spec in world.specs()}


def test_안내문이_서버에_실린다(server):
    assert server.instructions == '안내문'


def test_스키마에_자리는_없다(server):
    schema = tools_of(server)['propose_thing'].parameters

    assert set(schema['properties']) == {'amount', 'note'}
    assert 'actor' not in str(schema), '자리는 모델이 고르는 것이 아니다.'


def test_docstring_이_설명이_된다(server):
    assert tools_of(server)['propose_thing'].description == '무언가를 제안한다.'


@pytest.mark.django_db(transaction=True)
def test_부르면_한_모양의_결과가_나온다(server):
    result = asyncio.run(server.call_tool('propose_thing', {'amount': 3}))
    payload = result.structured_content

    assert payload['kind'] == Verdict.ALLOW
    assert payload['outcome'] == Outcome.COMMITTED
    assert payload['thing'] == {'id': 1}
