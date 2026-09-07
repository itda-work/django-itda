"""실접속(live) 트랙 — **패키지 경로**가 API 와 같은 판정을 내는가.

이 파일은 **채점표가 아니다.** 단계 테스트(`stage_NN_*.py`)가 채점표고,
여기는 `tests/live_api.py` 와 같은 성격의 회귀 테스트다. 거기는 HTTP 문을
두드리고 여기는 도구면을 부른다 — **판정은 같아야 한다.**

세 문(콘솔·API·도구면)이 같은 서비스 함수를 지나므로 다를 이유가 없고,
다르면 그건 법이 문마다 갈라졌다는 뜻이다.
"""

import pytest
from django.contrib.auth import get_user_model

from agent.live.tools import toolset
from django_itda.models import ToolCall
from django_itda.results import ToolDenied
from orders.models import Order, Refund
from orders.verdict import Outcome, Verdict
from shop.models import Product

User = get_user_model()


@pytest.fixture
def ai(world):
    return User.objects.get(username='ai-staff')


@pytest.fixture
def owner(world):
    return User.objects.get(username='owner')


def call(name, actor, **args):
    """도구면을 부른다. `via` 는 부르는 쪽이 준다 — 여기서는 MCP 경로다."""
    return toolset.call(name, actor, via=ToolCall.Via.MCP, **args)


def only_row(tool):
    rows = list(ToolCall.objects.filter(tool=tool))
    assert len(rows) == 1, f'{tool} 궤적이 {len(rows)}행이다.'
    return rows[0]


# --- 1. 권한 — 자격이 없으면 판정에 닿지 못한다 ---------------------------------


@pytest.mark.django_db
def test_AI_직원은_확정할_수_없다(ai):
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = call('propose_refund', ai, order_id=order.pk)['refund']['id']

    with pytest.raises(ToolDenied) as raised:
        call('approve_refund', ai, refund_id=refund_id)

    assert '권한 없음' in str(raised.value)
    assert 'AI 직원은 제안할 수 있지만 확정할 수 없다' in str(raised.value)
    assert Refund.objects.get(pk=refund_id).status == Refund.Status.PROPOSED
    assert only_row('approve_refund').error == ToolCall.Error.FORBIDDEN


@pytest.mark.django_db
def test_점주는_확정할_수_있고_주문이_취소로_간다(ai, owner):
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = call('propose_refund', ai, order_id=order.pk)['refund']['id']

    result = call('approve_refund', owner, refund_id=refund_id)

    assert result['kind'] == Verdict.ALLOW
    assert result['outcome'] == Outcome.COMMITTED
    assert result['refund']['status'] == Refund.Status.APPROVED
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED


# --- 2. 세계의 상태가 거부하는 자리 ---------------------------------------------


@pytest.mark.django_db
def test_8일_지난_주문_환불은_거부이고_규칙_ID_가_문장에_실린다(ai):
    order = Order.objects.get(order_number='SEED-0001')

    with pytest.raises(ToolDenied) as raised:
        call('propose_refund', ai, order_id=order.pk, reason='늦게 확인함')

    assert 'REFUND-001@v1' in str(raised.value)
    assert '대신 할 수 있는 것' in str(raised.value), '거절만 하면 모델은 우회를 시도한다.'
    assert not Refund.objects.filter(order=order).exists()

    row = only_row('propose_refund')
    assert row.error == ToolCall.Error.DENIED
    assert row.kind == Verdict.DENY
    assert row.rule_ids == ['REFUND-001@v1'], '거부도 궤적에 남는다.'


@pytest.mark.django_db
def test_이미_결제된_주문의_재결제는_거부이고_재고는_그대로다(ai):
    order = Order.objects.get(order_number='SEED-0002')
    stocks = {product.pk: product.stock for product in Product.objects.all()}

    with pytest.raises(ToolDenied) as raised:
        call('pay_order', ai, order_id=order.pk)

    assert 'ORDER-001@v1' in str(raised.value)
    order.refresh_from_db()
    assert order.status == Order.Status.PAID
    assert {product.pk: product.stock for product in Product.objects.all()} == stocks


# --- 3. 격상과 재전송 — 발견 1 의 회귀 ------------------------------------------


@pytest.mark.django_db
def test_5만원_초과는_격상되고_승인_핸들이_실린다(ai):
    order = Order.objects.get(order_number='SEED-0002')

    result = call('propose_refund', ai, order_id=order.pk, request_id='k', reason='사이즈 불일치')

    assert result['kind'] == Verdict.ESCALATE
    assert result['outcome'] == Outcome.QUEUED
    assert result['rule_ids'] == ['REFUND-002@v1']
    assert result['handle']['check_tool'] == 'check_refund'
    assert result['handle']['id'] == result['refund']['id']
    assert result['handle']['status'] == Refund.Status.PROPOSED

    order.refresh_from_db()
    assert order.status == Order.Status.PAID, '제안만으로는 세계가 안 움직인다.'


@pytest.mark.django_db
def test_같은_키_재전송은_REPLAYED_다(ai):
    """임시 어댑터는 여기서 `outcome` 을 통째로 떨어뜨렸다(관찰 2026-09-06 발견 1).

    도구 결과가 처음 요청과 재전송을 구분하지 못하면, 모델은 자기가 두 번째로
    보냈다는 사실을 알 수 없다. 결과 모양이 하나라는 것이 그 회귀를 막는다.
    """
    order = Order.objects.get(order_number='SEED-0002')

    first = call('propose_refund', ai, order_id=order.pk, request_id='k')
    again = call('propose_refund', ai, order_id=order.pk, request_id='k')

    assert first['outcome'] == Outcome.QUEUED
    assert again['outcome'] == Outcome.REPLAYED, '같은 요청이 다시 온 것이다.'
    assert again['kind'] == first['kind']
    assert again['refund']['id'] == first['refund']['id']
    assert Refund.objects.filter(order=order).count() == 1


@pytest.mark.django_db
def test_다른_키로_다시_제안하면_지금_상태를_듣는다(ai):
    order = Order.objects.get(order_number='SEED-0002')
    call('propose_refund', ai, order_id=order.pk, request_id='k')

    other = call('propose_refund', ai, order_id=order.pk, request_id='다른-키')

    assert other['outcome'] == Outcome.ALREADY, '다른 요청인데 이미 처리된 건이 있다.'
    assert Refund.objects.filter(order=order).count() == 1


# --- 4. 조회는 질문에 답한다 ----------------------------------------------------


@pytest.mark.django_db
def test_조회_도구는_거부된_건에도_예외를_던지지_않는다(ai, owner):
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = call('propose_refund', ai, order_id=order.pk)['refund']['id']
    Refund.objects.filter(pk=refund_id).update(status=Refund.Status.REJECTED)

    result = call('check_refund', ai, refund_id=refund_id)

    assert result['kind'] == Verdict.DENY
    assert result['outcome'] == Outcome.ALREADY
    assert only_row('check_refund').error == '', '질문에 답한 것은 오류가 아니다.'


@pytest.mark.django_db
def test_승인_대기_건은_폴링으로_확정을_확인한다(ai, owner):
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = call('propose_refund', ai, order_id=order.pk)['refund']['id']

    waiting = call('check_refund', ai, refund_id=refund_id)
    call('approve_refund', owner, refund_id=refund_id)
    settled = call('check_refund', ai, refund_id=refund_id)

    assert waiting['kind'] == Verdict.ESCALATE
    assert settled['kind'] == Verdict.ALLOW, '푸시는 없다 — 물어봐야 안다.'


@pytest.mark.django_db
def test_판정_없는_조회에는_판정_어휘가_없다(ai):
    result = call('list_orders', ai)

    numbers = {order['order_number'] for order in result['orders']}
    assert {'SEED-0001', 'SEED-0002'} <= numbers
    assert 'kind' not in result and 'outcome' not in result


# --- 5. 접수 · 결제 --------------------------------------------------------------


@pytest.mark.django_db
def test_주문을_접수하면_결제_대기_주문이_하나_생긴다(ai):
    before = Order.objects.count()

    result = call('place_order', ai)

    assert result['kind'] == Verdict.ALLOW
    assert result['outcome'] == Outcome.COMMITTED
    assert result['order']['status'] == Order.Status.PENDING
    assert Order.objects.count() == before + 1


@pytest.mark.django_db
def test_장바구니를_못_읽으면_판정이_아니라_오류다(ai):
    with pytest.raises(Product.DoesNotExist):
        call('place_order', ai, items=[{'product': '없는 상품', 'quantity': 1}])

    assert only_row('place_order').error == ToolCall.Error.EXCEPTION


@pytest.mark.django_db
def test_결제_대기_주문은_결제된다(ai):
    order = Order.objects.get(order_number='SEED-0004')

    result = call('pay_order', ai, order_id=order.pk)

    assert result['kind'] == Verdict.ALLOW
    assert result['outcome'] == Outcome.COMMITTED
    assert result['order']['status'] == Order.Status.PAID


# --- 6. 궤적 — 세 문이 같은 표에 남는다 ------------------------------------------


@pytest.mark.django_db
def test_호출마다_궤적_한_행이_남고_결과와_call_id_로_이어진다(ai):
    order = Order.objects.get(order_number='SEED-0002')

    result = call('propose_refund', ai, order_id=order.pk, request_id='k', reason='사이즈')

    row = only_row('propose_refund')
    assert row.call_id == result['call_id']
    assert row.actor == ai
    assert row.via == ToolCall.Via.MCP
    assert row.arguments == {'order_id': order.pk, 'request_id': 'k', 'reason': '사이즈'}
    assert row.kind == Verdict.ESCALATE
    assert row.outcome == Outcome.QUEUED
    assert row.rule_ids == ['REFUND-002@v1']


@pytest.mark.django_db
def test_admin_경로도_같은_표에_남길_수_있다(ai, owner):
    """발견 3 — admin 커스텀 액션은 `LogEntry` 를 남기지 않는다.

    7단계가 admin 승인에서 이 표에 `via='admin'` 으로 기록하면, "누가 무엇을
    언제 어느 문으로" 가 처음으로 한 표에서 읽힌다.
    """
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = call('propose_refund', ai, order_id=order.pk)['refund']['id']

    toolset.call('approve_refund', owner, via=ToolCall.Via.ADMIN, refund_id=refund_id)

    assert only_row('approve_refund').via == ToolCall.Via.ADMIN


@pytest.mark.django_db
def test_점주는_admin_에서_궤적을_읽을_수_있다(client, ai, owner):
    """403 은 코드가 아니라 데이터다 — 점주 그룹에 `view_toolcall` 이 시드돼 있다."""
    order = Order.objects.get(order_number='SEED-0002')
    call('propose_refund', ai, order_id=order.pk)

    client.force_login(owner)
    response = client.get('/admin/django_itda/toolcall/')

    assert response.status_code == 200


# --- 7. 도구 목록 ---------------------------------------------------------------


@pytest.mark.django_db
def test_부를_수_없는_도구도_목록에_보인다(ai):
    names = [spec.name for spec in toolset.specs(actor=ai)]

    assert len(names) == 7
    assert 'approve_refund' in names, '목록에 있다는 것과 부를 수 있다는 것은 다른 얘기다.'
    assert [spec.name for spec in toolset.specs(actor=ai, visible_only=True)] == [
        name for name in names if name != 'approve_refund'
    ]
