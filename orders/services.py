"""업무 동작 — 콘솔(HTML)과 API(JSON)가 **같은 문을 지나게** 하는 자리.

여기 있는 함수는 세 개뿐이고, 셋 다 판정 로직을 갖고 있지 않다.
판정은 `Order.mark_paid`·`Refund.decide/apply` 가 하고, 이 모듈은 그 결과를
그대로 넘긴다. 이 층이 필요한 이유는 하나다 — **입구가 둘이면 법이 갈라진다.**
콘솔 뷰와 MCP 도구가 각자 환불 절차를 구현하면, 한쪽만 고쳐진 순간부터
"세계의 법"이 아니라 "그 화면의 법"이 된다.
"""

from django.db import transaction

from .models import InvalidTransition, Order, OrderItem, Refund
from .verdict import Verdict


@transaction.atomic
def intake_order(customer, lines, shipping):
    """주문을 접수한다 — 결제 대기 상태의 주문 하나.

    `lines` 는 `[(Product, 수량)]`. 상품명·단가는 지금 값을 스냅샷으로 박는다.
    """
    order = Order.objects.create(
        user=customer,
        status=Order.Status.PENDING,
        total_amount=sum(product.price * quantity for product, quantity in lines),
        **shipping,
    )
    for product, quantity in lines:
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
        )
    return order


def propose_refund(actor, order, amount, reason):
    """환불을 제안한다. `(Verdict, Outcome)`.

    이미 살아 있는 제안이 있으면 **다시 판정하지 않고** 지금 처리 상태를 답한다.
    세계가 중복을 막아 주는 것이 아니다 — 여기서 걸러 줄 뿐이고, 그 얘기는 5단계다.
    """
    existing = Refund.objects.filter(order=order).exclude(status=Refund.Status.REJECTED).first()
    if existing:
        return existing.current_outcome()
    return Refund.apply(order=order, amount=amount, reason=reason, requested_by=actor)


def pay_order(order):
    """결제 처리한다. 전이 계약이 거부하면 그 판정을 그대로 돌려준다."""
    try:
        order.mark_paid()
    except InvalidTransition as denied:
        return denied.verdict
    return Verdict(
        kind=Verdict.ALLOW,
        reason=f'{order.order_number} 을(를) 결제 완료로 옮겼습니다.',
    )
