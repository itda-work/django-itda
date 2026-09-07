"""업무 동작 — 콘솔(HTML)과 API(JSON)가 **같은 문을 지나게** 하는 자리.

여기 있는 함수는 넷뿐이고, 넷 다 판정 로직을 갖고 있지 않다.
판정은 `Order.mark_paid`·`Refund.decide/apply` 가 하고, 이 모듈은 그 결과를
그대로 넘긴다. 이 층이 필요한 이유는 하나다 — **입구가 둘이면 법이 갈라진다.**
콘솔 뷰와 MCP 도구가 각자 환불 절차를 구현하면, 한쪽만 고쳐진 순간부터
"세계의 법"이 아니라 "그 화면의 법"이 된다.

7단계에서 장부는 이 층이 아니라 **전이 메서드 안**으로 갔다. 입구가 하나여도
`Refund.approve` 를 admin 액션이 직접 부르면 이 층을 지나지 않기 때문이다.
여기 남은 기록은 하나뿐이다 — 접수(`order.placed`)와 링크 발급
(`payment_link.issued`). 둘 다 전이 메서드가 없는 사실이라 여기가 그 자리다.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ledger.models import Event

from .models import InvalidTransition, Order, OrderItem, Refund
from .rules import ORDER_001, PAY_001, RULE_TEXTS
from .tokens import payment_token
from .verdict import Verdict


@transaction.atomic
def intake_order(customer, lines, shipping):
    """주문을 접수한다 — 결제 대기 상태의 주문 하나.

    `lines` 는 `[(Product, 수량)]`. 상품명·단가는 지금 값을 스냅샷으로 박는다.

    장부는 **생성 뒤, 같은 트랜잭션 안**이다. 접수가 롤백되면 기록도 함께
    되돌아간다 — 없는 주문의 접수 기록이 남는 일은 없다.
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
    Event.record(
        subject=order,
        transition=Event.Transition.ORDER_PLACED,
        kind=Event.Kind.ALLOW,
        reason=f'주문 {order.order_number} 을(를) 접수했다.',
        after={'status': order.status, 'total_amount': order.total_amount},
        actor=customer,
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

    **장부는 여기서 적지 않는다.** 제안이 실제로 만들어졌을 때만 사실이고,
    그것을 아는 자리는 `Refund.apply` 안이다 — 거부·재전송·기존 건 응답은
    세계를 바꾸지 않으므로 도메인 장부에 행이 없다(불린 사실은 `ToolCall`).
    """
    return Refund.apply(
        order=order,
        amount=amount,
        reason=reason,
        requested_by=actor,
        idempotency_key=idempotency_key,
    )


def pay_order(actor, order):
    """결제 처리한다. 전이 계약이 거부하면 그 판정을 그대로 돌려준다.

    `actor` 는 **결제를 확정한 사람**이다. 6단계부터 그 사람은 고객이고,
    결제 페이지가 `request.user` 를 여기로 넘긴다(7단계). 장부는 이 층이
    아니라 `Order.mark_paid` 안에서 적힌다 — 사실이 나는 자리가 기록의
    자리다.
    """
    try:
        order.mark_paid(actor)
    except InvalidTransition as denied:
        return denied.verdict
    return Verdict(
        kind=Verdict.ALLOW,
        reason=f'{order.order_number} 을(를) 결제 완료로 옮겼습니다.',
    )


def issue_payment_link(actor, order):
    """결제 링크를 발급한다. `(Verdict, dict | None)`.

    **결제를 완료시키지 않는다.** 결제는 돈을 내는 사람의 확정이라 AI 직원이
    대신 누를 일이 아니다 — AI 의 문에서는 여기까지고, `Order.mark_paid` 를
    부르는 곳은 고객의 결제 페이지 하나다. 권한이 막는 것이 아니라 **경로**가
    갈린다.

    결제 대기 상태가 아니면 링크를 만들지 않는다. `mark_paid` 가 쓰는 것과
    **같은 규칙 ID** 다 — 같은 문장이 다른 자리에서 답한다.

    발급 사실은 장부에 남는다 — 6단계에서 자리만 뚫어 두었던 `actor` 인자가
    여기서 쓰인다("누가 링크를 발급했나"). 링크 자체는 여전히 무상태다.
    발급이 **일어났을 때만**, 즉 격상 판정 뒤에 적는다.
    """
    if order.status != Order.Status.PENDING:
        return (
            Verdict(
                kind=Verdict.DENY,
                rule_ids=[ORDER_001],
                reason=(
                    f'{ORDER_001}: 결제 대기 상태가 아니다 — 링크를 발급하지 않는다. '
                    f'{RULE_TEXTS[ORDER_001]}'
                ),
                alternatives=['주문 내역에서 이 주문의 현재 상태를 조회한다'],
            ),
            None,
        )
    payment = _payment_payload(order)
    verdict = Verdict(
        kind=Verdict.ESCALATE,
        rule_ids=[PAY_001],
        reason=(
            f'{PAY_001}: 결제는 고객이 한다. 결제 링크를 발급했다 — '
            f'1시간 안에 고객이 결제해야 한다. {RULE_TEXTS[PAY_001]}'
        ),
        alternatives=['고객에게 링크를 안내하고 결제 완료를 주문 조회로 확인한다'],
    )
    # **격상일 때만** 적는다. 발급하지 않은 링크가 장부에 남으면, 장부는
    # 일어난 일이 아니라 시도된 일을 적은 것이다.
    Event.record(
        subject=order,
        transition=Event.Transition.PAYMENT_LINK_ISSUED,
        kind=Event.Kind.ESCALATE,
        rule_ids=[PAY_001],
        reason=verdict.reason,
        after={'expires_at': payment['expires_at']},
        actor=actor,
    )
    return verdict, payment


def _payment_payload(order):
    """링크와 만료 시각.

    `expires_at` 은 **정보용**이다 — 화면에 띄우고 모델에게 알려 줄 숫자이지,
    이 값으로 판정하지 않는다. 정본은 토큰이고, 토큰이 스스로 시각을 들고 다닌다.
    """
    url = settings.WORLD_PUBLIC_URL + reverse(
        'pay', args=[order.pk, payment_token.make_token(order)]
    )
    expires_at = timezone.now() + timedelta(seconds=payment_token.timeout)
    return {'url': url, 'expires_at': expires_at.isoformat()}
