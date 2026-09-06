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


def propose_refund(actor, order, amount, reason, idempotency_key=''):
    """환불을 제안한다. `(Verdict, Outcome)`.

    **여기서 걸러 주지 않는다.** 4단계에는 "이미 있는지 조회하고 없으면 만든다"는
    줄이 있었는데, 그 줄은 중복을 막은 것이 아니라 **동시에 오지 않는 요청만**
    막고 있었다. 조회와 생성 사이에 벌어지는 틈은 애플리케이션이 없앨 수 없다.

    그래서 5단계에서 지웠다. 중복은 세계가 막는다 —
    `Refund` 의 부분 유일 제약(REFUND-003@v1)이 INSERT 를 거절하고,
    `Refund.apply` 가 그 거절을 판정으로 번역한다.

    `idempotency_key` 는 클라이언트가 "이건 아까 그 요청이다"라고 말하는 방법이다.
    주면 재전송에 **그때의 답**이 돌아오고, 안 주면 두 번째 요청은 **지금 상태**를 듣는다.
    """
    return Refund.apply(
        order=order,
        amount=amount,
        reason=reason,
        requested_by=actor,
        idempotency_key=idempotency_key,
    )


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
