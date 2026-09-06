"""실접속(live) 트랙 — **패키지 경로**의 도구면 선언.

`mcp_server.py`(임시)와 같은 도구 7개를 선언하지만, 붙는 방식이 다르다.

    임시(HTTP)   Claude → stdio 프로세스 → HTTP /api/… → services → 판정
    패키지        Claude → manage.py mcp_stdio (Django 프로세스) → services → 판정

둘 다 **같은 서비스 함수**를 지난다(`orders/services.py`). 그래서 판정은 한 글자도
다르지 않다. 다른 것은 셋이다.

1. **결과 모양이 하나다.** HTTP 코드마다 다른 조립기가 없다. `kind`·`outcome` 은
   늘 실린다(관찰 2026-09-06 발견 1 — 임시 어댑터가 202 에서 그 둘을 떨어뜨렸다).
2. **궤적이 남는다.** 호출마다 `django_itda.ToolCall` 한 행이고, admin 에서 읽는다.
3. **표현을 다시 쓰지 않는다.** `order_json`·`refund_json` 은 `orders/api.py` 것을
   그대로 임포트한다 — 콘솔·API·MCP 가 같은 표현을 쓴다. 두 벌이 되는 순간
   한쪽만 고쳐지고, 그때부터 "세계의 표현" 이 아니라 "그 문의 표현" 이 된다.

이 파일은 **판정을 한 줄도 하지 않는다.** 서비스 함수를 부르고, 그 답을 그대로 넘긴다.
권한 문장은 `orders/api.py` 와 글자까지 같다 — 같은 세계가 문마다 다른 말을 하면 안 된다.
"""

from pathlib import Path

from django.contrib.auth import get_user_model

from agent.scenarios import ALICE_CART, ALICE_SHIPPING
from django_itda.tools import Toolset
from orders import services
from orders.api import _resolve_lines, order_json, refund_json
from orders.models import InvalidTransition, Order, Refund
from orders.verdict import Outcome, Verdict

User = get_user_model()

# AI 직원 시스템 프롬프트를 그대로 서버 안내문으로 싣는다.
#
# 여기 실린 것은 **부탁**이다. 부탁 중 어떤 문장이 실제로 코드에 이사됐는지는
# 프롬프트 자신이 표시해 두고 있고, 이사 표시가 없는 문장은 아무도 지켜 주지 않는다.
# 그 대조가 8단계 세계 부채 리포트의 입력이다 — 그래서 요약하지 않고 원문을 싣는다.
PROMPT_PATH = Path(__file__).resolve().parent.parent / 'prompts' / 'ai_staff.md'

toolset = Toolset(name='itda-world', instructions=PROMPT_PATH.read_text(encoding='utf-8'))


def _order(order_id):
    """주문 하나를 찾는다. 없으면 **판정이 아니라 오류**다(404 에 해당)."""
    try:
        return Order.objects.get(pk=order_id)
    except Order.DoesNotExist:
        raise ValueError(f'세계에 그런 주문이 없다: order_id={order_id}') from None


def _refund(refund_id):
    try:
        return Refund.objects.select_related('order').get(pk=refund_id)
    except Refund.DoesNotExist:
        raise ValueError(f'세계에 그런 환불이 없다: refund_id={refund_id}') from None


# --- 주문 --------------------------------------------------------------------


@toolset.tool(perm='orders.view_order', query=True)
def list_orders(actor) -> dict:
    """가게의 주문 목록을 읽는다.

    주의: 지금 이 세계에는 소유권 스코핑이 없다. 권한이 있으면 모든 고객의
    주문이 보인다(교육용으로 남겨 둔 구멍이다).
    """
    orders = Order.objects.order_by('-created_at')
    return {'orders': [order_json(order) for order in orders]}


@toolset.tool(perm='orders.view_order', query=True)
def get_order(actor, order_id: int) -> dict:
    """주문 한 건의 상세 — 상품·금액·상태·걸려 있는 환불 제안."""
    order = _order(order_id)
    payload = order_json(order)
    payload['items'] = [
        {
            'product_name': item.product_name,
            'unit_price': item.unit_price,
            'quantity': item.quantity,
        }
        for item in order.items.all()
    ]
    payload['refunds'] = [refund_json(refund) for refund in order.refunds.all()]
    return payload


@toolset.tool(perm='orders.add_order', forbidden_reason='주문 접수 권한이 없습니다.')
def place_order(actor, items: list[dict] | None = None):
    """주문을 접수한다. 결제 대기 상태의 주문이 하나 생긴다.

    `items` 는 `[{"product": "상품명", "quantity": 1}]`. 생략하면 고객 alice 의
    기본 장바구니로 접수한다. 주문자는 언제나 고객이다 — 대신 넣어 주는 것이다.
    """
    # 장바구니를 못 읽은 것은 판정이 아니라 **잘못된 요청**이다(400 에 해당).
    # 거부로 포장하면 모델은 있지도 않은 규칙을 찾아 헤맨다.
    lines = _resolve_lines(items or ALICE_CART)
    customer = User.objects.get(username='alice')
    order = services.intake_order(customer, lines, ALICE_SHIPPING)
    verdict = Verdict(
        kind=Verdict.ALLOW,
        reason=(
            f'주문 {order.order_number} 을(를) {order.get_status_display()} 상태로 '
            f'접수했습니다. 아무도 승인하지 않았습니다 — AI 직원의 권한 안입니다.'
        ),
    )
    return verdict, Outcome.COMMITTED, {'order': order_json(order)}


@toolset.tool(perm='orders.change_order', forbidden_reason='결제 처리 권한이 없습니다.')
def pay_order(actor, order_id: int):
    """결제 처리를 한다.

    결제 대기 상태의 주문만 결제 완료로 갈 수 있다. 이미 결제된 주문에 다시
    부르면 세계가 거부한다(ORDER-001@v1) — 권한 문제가 아니라 상태 문제다.
    """
    order = _order(order_id)
    verdict = services.pay_order(order)
    order.refresh_from_db()
    state = Outcome.COMMITTED if verdict.kind == Verdict.ALLOW else Outcome.NOTHING
    return verdict, state, {'order': order_json(order)}


# --- 환불 --------------------------------------------------------------------


@toolset.tool(
    perm='orders.add_refund',
    forbidden_reason='환불 제안 권한이 없습니다.',
    handle_tool='check_refund',
)
def propose_refund(
    actor,
    order_id: int,
    amount: int | None = None,
    reason: str = '고객 요청',
    request_id: str | None = None,
):
    """환불을 제안한다. `amount` 를 생략하면 전액.

    세 갈래로 답이 온다.
    - 규칙이 확정 — 기한 안이고 소액이면 바로 승인된다(`kind` ALLOW · `outcome` COMMITTED).
    - 점주 승인 대기 — 5만원을 넘으면 제안만 올라간다(ESCALATE · QUEUED).
      `handle` 에 확인용 도구와 대상 ID 가 실린다.
    - 거부 — 결제 후 7일이 지난 주문은 환불되지 않는다(REFUND-001@v1). 도구 오류다.

    같은 요청을 다시 보낼 때는 **같은 `request_id`** 를 써라. 새 환불이 아니라
    그때의 답을 다시 받는다 — `outcome` 이 `REPLAYED` 다. `request_id` 를 바꿔서
    다시 보내면 그건 다른 요청이고, 한 주문에 살아 있는 환불은 하나뿐이므로
    세계는 기존 건의 **지금 상태**(`ALREADY`)로 답한다.
    """
    order = _order(order_id)
    amount = int(amount) if amount is not None else order.total_amount
    if amount < 1:
        raise ValueError('amount 는 1 이상이어야 합니다.')
    key = str(request_id or '')
    if len(key) > 64:
        raise ValueError('멱등키는 64자 이하여야 합니다.')

    verdict, outcome = services.propose_refund(
        actor, order, amount, str(reason)[:200], key
    )
    objects = {'order': order_json(order)}
    if outcome.refund is not None:
        objects['refund'] = refund_json(outcome.refund)
    return verdict, outcome.state, objects


@toolset.tool(perm='orders.view_refund', query=True, forbidden_reason='환불 열람 권한이 없습니다.')
def check_refund(actor, refund_id: int):
    """환불 제안이 지금 어떤 상태인지 묻는다 — 승인 대기 건의 폴링용.

    알림은 오지 않는다. 점주가 admin 에서 누르면 여기 상태가 바뀔 뿐이다.
    거부된 건이어도 **오류가 아니라 결과**로 답한다 — 질문에 답을 안 하는 것은
    판정이 아니다. `kind` 가 ESCALATE 면 아직 대기, ALLOW 면 확정, DENY 면 거부다.
    """
    refund = _refund(refund_id)
    verdict, outcome = refund.current_outcome()
    return (
        verdict,
        outcome.state,
        {'refund': refund_json(refund), 'order': order_json(refund.order)},
    )


@toolset.tool(
    perm='orders.change_refund',
    forbidden_reason='AI 직원은 제안할 수 있지만 확정할 수 없다',
)
def approve_refund(actor, refund_id: int):
    """환불을 **확정**한다. 점주 권한(`orders.change_refund`)이 필요하다.

    AI 직원 계정으로는 거부된다. 제안까지가 AI 직원의 자리고, 확정은 점주의
    자리다 — 이 도구가 목록에 보인다는 것과 부를 수 있다는 것은 다른 얘기다.
    """
    refund = _refund(refund_id)
    # 상태를 **먼저 조회해서** 검사하지 않는다. 조회와 확정 사이가 벌어지면
    # 점주 둘이 동시에 눌렀을 때 둘 다 통과한다. 확정을 먼저 시도하고,
    # 조건에 안 맞으면 세계가 거절한다.
    try:
        refund.approve(actor)
    except InvalidTransition as blocked:
        refund.refresh_from_db()
        return blocked.verdict, Outcome.ALREADY, {'refund': refund_json(refund)}
    refund.refresh_from_db()
    verdict = Verdict(
        kind=Verdict.ALLOW,
        reason=(
            f'{refund.order.order_number} 환불 {refund.amount:,}원을 승인했습니다. '
            f'주문이 취소로 바뀌었습니다.'
        ),
    )
    return (
        verdict,
        Outcome.COMMITTED,
        {'refund': refund_json(refund), 'order': order_json(refund.order)},
    )

