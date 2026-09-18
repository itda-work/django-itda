"""호출 경로 — 권한 → 실행 → 매핑 → 기록.

더미 도구로 본다. 도메인이 없어도 이 경로는 서야 한다 — 패키지가 판정을
한 줄도 하지 않는다는 것이 여기서 확인된다.
"""

import dataclasses

import pytest
from django.contrib.auth.models import Permission, User
from django.db import OperationalError

from django_itda.context import current_call
from django_itda.models import ToolCall
from django_itda.results import CONTRACT_VERSION, ToolBusy, ToolDenied
from django_itda.tools import Toolset
from django_itda.verdict import Outcome, Verdict

ESCALATED = Verdict(kind=Verdict.ESCALATE, rule_ids=['R-002@v1'], reason='사람에게 올린다')
DENIED = Verdict(kind=Verdict.DENY, rule_ids=['R-001@v1'], reason='세계가 그 상태가 아니다')


@pytest.fixture
def toolset():
    world = Toolset(name='더미-세계', instructions='안내문')

    @world.tool(perm='auth.add_user', handle_tool='check_thing')
    def propose_thing(actor, amount: int = 1, note: str = '메모'):
        """무언가를 제안한다."""
        return ESCALATED, Outcome.QUEUED, {'refund': {'id': 7, 'status': 'proposed'}}

    @world.tool(perm='auth.add_user', handle_tool='check_thing')
    def propose_link(actor):
        """핸들에 사람이 열 URL 을 보태는 도구."""
        return (
            ESCALATED,
            Outcome.QUEUED,
            {
                'refund': {'id': 7, 'status': 'proposed'},
                'handle': {'url': 'http://세계/열어라/', 'expires_at': '2026-09-07T12:00:00'},
            },
        )

    @world.tool(perm='auth.add_user', handle_tool='check_thing')
    def settle_thing(actor):
        """확정하면서도 핸들 필드를 실어 보내는 도구 — 기다릴 것이 없다."""
        return (
            Verdict(kind=Verdict.ALLOW),
            Outcome.COMMITTED,
            {'refund': {'id': 7, 'status': 'approved'}, 'handle': {'url': 'http://세계/열어라/'}},
        )

    @world.tool(perm='auth.change_user', forbidden_reason='제안까지가 네 자리다')
    def confirm_thing(actor, thing_id: int):
        """무언가를 확정한다."""
        return Verdict(kind=Verdict.ALLOW), Outcome.COMMITTED, {}

    @world.tool
    def deny_thing(actor):
        """언제나 거부되는 도구."""
        return DENIED, Outcome.NOTHING, {}

    @world.tool(query=True)
    def check_thing(actor):
        """조회 도구 — 무엇이 나오든 결과로 돌려준다."""
        return DENIED, Outcome.ALREADY, {}

    @world.tool(query=True)
    def list_things(actor):
        """판정 없는 순수 조회."""
        return {'things': [1, 2]}

    @world.tool(query=True)
    def list_impostor(actor):
        """봉투 키와 같은 이름을 싣는 순수 조회."""
        return {'call_id': '가짜', 'contract_version': 99, 'things': [3]}

    @world.tool(query=True)
    def peek_context(actor):
        """도구 본문에서 호출 문맥을 들여다본다(v0.2)."""
        return {'context': dataclasses.asdict(current_call()) | {'actor': None}}

    @world.tool
    def break_thing(actor, how: str = 'lock'):
        """터지는 도구."""
        raise OperationalError('database is locked' if how == 'lock' else 'no such table: x')

    return world


@pytest.fixture
def actor(db):
    user = User.objects.create_user('직원')
    user.user_permissions.add(Permission.objects.get(codename='add_user'))
    return User.objects.get(pk=user.pk)  # 권한 캐시를 비운다


def only_call():
    calls = list(ToolCall.objects.all())
    assert len(calls) == 1, f'궤적이 {len(calls)}행이다 — 호출 한 번은 한 행이다.'
    return calls[0]


# --- 권한 -----------------------------------------------------------------------


def test_권한이_없으면_판정에_닿지_못한다(toolset, actor):
    with pytest.raises(ToolDenied) as raised:
        toolset.call('confirm_thing', actor, thing_id=1)

    assert str(raised.value) == '세계가 거부했다 — 권한 없음: 제안까지가 네 자리다'
    row = only_call()
    assert row.error == ToolCall.Error.FORBIDDEN
    assert row.kind == '' and row.outcome == '', '판정한 적이 없다.'


def test_권한_거부_문장의_기본값은_도구_이름이다(toolset, db):
    민숭이 = User.objects.create_user('민숭이')

    with pytest.raises(ToolDenied) as raised:
        toolset.call('propose_thing', 민숭이)

    assert '권한 없음: propose_thing 을(를) 부를 권한이 없다' in str(raised.value)


def test_perm_이_없는_도구는_아무나_부른다(toolset, db):
    나그네 = User.objects.create_user('나그네')

    result = toolset.call('list_things', 나그네)

    assert result['things'] == [1, 2]


# --- 매핑 -----------------------------------------------------------------------


def test_ESCALATE_는_승인_핸들을_받는다(toolset, actor):
    result = toolset.call('propose_thing', actor, amount=5)

    assert result['kind'] == Verdict.ESCALATE
    assert result['outcome'] == Outcome.QUEUED
    assert result['handle'] == {'check_tool': 'check_thing', 'id': 7, 'status': 'proposed'}
    assert result['call_id'] == only_call().call_id, '결과와 궤적은 call_id 로 이어진다.'


def test_도구가_핸들에_필드를_보탠다(toolset, actor):
    result = toolset.call('propose_link', actor)

    assert result['handle'] == {
        'check_tool': 'check_thing',
        'id': 7,
        'status': 'proposed',
        'url': 'http://세계/열어라/',
        'expires_at': '2026-09-07T12:00:00',
    }, '자동 핸들 위에 도구가 보탠 것이 얹힌다 — 최상위 handle 은 하나다.'
    assert 'handle' not in result['refund'], '보탠 것은 objects 에서 빠진다.'


def test_격상이_아니면_핸들은_없다(toolset, actor):
    result = toolset.call('settle_thing', actor)

    assert result['handle'] is None, '기다릴 것이 없는 답에 기다리는 방법을 싣지 않는다.'
    assert result['refund'] == {'id': 7, 'status': 'approved'}


def test_실행_도구의_DENY_는_오류다(toolset, actor):
    with pytest.raises(ToolDenied) as raised:
        toolset.call('deny_thing', actor)

    assert '[R-001@v1]' in str(raised.value)
    row = only_call()
    assert row.error == ToolCall.Error.DENIED
    assert row.kind == Verdict.DENY
    assert row.rule_ids == ['R-001@v1'], '거부도 규칙 ID 와 함께 남는다.'


def test_조회_도구는_DENY_여도_결과를_준다(toolset, actor):
    result = toolset.call('check_thing', actor)

    assert result['kind'] == Verdict.DENY
    assert result['outcome'] == Outcome.ALREADY
    assert only_call().error == '', '질문에 답한 것은 오류가 아니다.'


def test_판정_없는_조회에는_판정_어휘를_지어내지_않는다(toolset, actor):
    result = toolset.call('list_things', actor)

    assert 'kind' not in result and 'outcome' not in result
    assert only_call().kind == ''


def test_순수_조회의_반환값은_봉투_키를_덮어쓸_수_없다(toolset, actor):
    result = toolset.call('list_impostor', actor)

    assert result['call_id'] == only_call().call_id != '가짜'
    assert result['contract_version'] == CONTRACT_VERSION
    assert result['things'] == [3]


def test_판정_없는_조회에도_계약_버전이_실린다(toolset, actor):
    result = toolset.call('list_things', actor)

    assert result['contract_version'] == CONTRACT_VERSION
    assert result['call_id'] == only_call().call_id


# --- 잠금과 고장 -------------------------------------------------------------------


def test_잠금_실패는_ToolBusy_다(toolset, actor):
    with pytest.raises(ToolBusy) as raised:
        toolset.call('break_thing', actor, how='lock')

    assert raised.value.retry_after == 1
    row = only_call()
    assert row.error == ToolCall.Error.BUSY
    assert row.kind == '', '판정하지 못한 것은 판정이 아니다.'


def test_알_수_없는_예외는_그대로_올라간다(toolset, actor):
    with pytest.raises(OperationalError) as raised:
        toolset.call('break_thing', actor, how='schema')

    assert 'no such table' in str(raised.value)
    row = only_call()
    assert row.error == ToolCall.Error.EXCEPTION
    assert 'OperationalError' in row.reason


# --- 궤적 -----------------------------------------------------------------------


def test_궤적은_자리와_인자와_경로를_남긴다(toolset, actor):
    toolset.call('propose_thing', actor, amount=3, note='사이즈 불일치', via=ToolCall.Via.ADMIN)

    row = only_call()
    assert row.actor == actor
    assert row.arguments == {'amount': 3, 'note': '사이즈 불일치'}
    assert row.via == ToolCall.Via.ADMIN, 'admin 경로도 같은 표에 남는다(발견 3).'
    assert row.duration_ms >= 0


# --- 목록 -----------------------------------------------------------------------


def test_목록은_기본으로_부를_수_없는_도구도_보여_준다(toolset, actor):
    names = [spec.name for spec in toolset.specs(actor=actor)]

    assert 'confirm_thing' in names, '목록에 있다는 것과 부를 수 있다는 것은 다른 얘기다.'


def test_visible_only_는_권한으로_거른다(toolset, actor):
    names = [spec.name for spec in toolset.specs(actor=actor, visible_only=True)]

    assert 'propose_thing' in names
    assert 'confirm_thing' not in names
    assert 'list_things' in names, 'perm 이 없는 도구는 거르지 않는다.'


def test_시그니처에서_자리를_뗄_수_있다(toolset):
    parameters = toolset.get('propose_thing').signature_without_actor().parameters

    assert list(parameters) == ['amount', 'note']
    assert parameters['note'].default == '메모'


# --- 문맥 (v0.2) ----------------------------------------------------------------


def test_도구_본문은_호출_문맥_안에서_돈다(toolset, actor):
    """도메인 코드가 `call_id` 를 인자로 받지 않고도 궤적과 같은 값을 읽는다.

    도메인 장부(itda-django 7단계 `ledger.Event`)가 이것으로 도구면 궤적과
    잇닿는다 — 같은 호출의 두 층이 같은 상관 ID 를 갖는다.
    """
    result = toolset.call('peek_context', actor, via=ToolCall.Via.ADMIN)

    assert result['context']['call_id'] == result['call_id'] == only_call().call_id
    assert result['context']['via'] == ToolCall.Via.ADMIN


def test_호출이_끝나면_문맥은_풀린다(toolset, actor):
    toolset.call('peek_context', actor)

    assert current_call().call_id == ''
    assert current_call().via == 'shell'


def test_거부된_호출은_문맥을_열지_않는다(toolset, db):
    """권한이 없으면 본문에 닿지 못한다 — 열리지 않은 문의 문맥도 없다."""
    with pytest.raises(ToolDenied):
        toolset.call('propose_thing', None)

    assert current_call().call_id == ''
