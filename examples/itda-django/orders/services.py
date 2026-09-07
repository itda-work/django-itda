"""업무 동작 — 콘솔(HTML)과 API(JSON)가 **같은 문을 지나게** 하는 자리.

여기 있는 함수는 넷뿐이고, 넷 다 판정 로직을 갖고 있지 않다.
판정은 `Order.mark_paid`·`Refund.decide/apply` 가 하고, 이 모듈은 그 결과를
그대로 넘긴다. 이 층이 필요한 이유는 하나다 — **입구가 둘이면 법이 갈라진다.**
콘솔 뷰와 MCP 도구가 각자 환불 절차를 구현하면, 한쪽만 고쳐진 순간부터
"세계의 법"이 아니라 "그 화면의 법"이 된다.

7단계에서 이 층이 **장부도 적는다.** 입구가 하나니 기록도 한 자리에 모으면
빠짐없이 남는다 — 는 것이 여기 적힌 이유이고, 그게 이 단계의 결함이다.
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


def intake_order(customer, lines, shipping):
    """주문을 접수한다 — 결제 대기 상태의 주문 하나.

    `lines` 는 `[(Product, 수량)]`. 상품명·단가는 지금 값을 스냅샷으로 박는다.

    장부는 `atomic` **밖에서** 적는다 — 트랜잭션이 끝난 뒤라 "정말 남았는지"
    확인하고 적는 것처럼 보인다. 그런데 여기서 프로세스가 죽으면 주문은 있고
    장부에는 없다. 사실과 기록이 다른 트랜잭션에 있으면 둘은 언제든 갈린다.
    """
    with transaction.atomic():
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
        reason='주문을 접수했다.',
        after={'status': order.status, 'total_amount': order.total_amount},
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
    # 장부를 **먼저** 적는다. 입구가 하나이므로 여기서 적으면 어느 문으로
    # 들어와도 빠지지 않는다 — 그렇게 보인다.
    Event.record(
        subject=order,
        transition=Event.Transition.REFUND_PROPOSED,
        kind=Event.Kind.ALLOW,
        reason=f'{order.order_number} 환불 {amount:,}원을 제안했다.',
        after={'status': Refund.Status.PROPOSED, 'amount': amount},
        actor=actor,
    )
    return Refund.apply(
        order=order,
        amount=amount,
        reason=reason,
        requested_by=actor,
        idempotency_key=idempotency_key,
    )


def pay_order(order):
    """결제 처리한다. 전이 계약이 거부하면 그 판정을 그대로 돌려준다.

    장부는 여기서 적는다 — 전이 메서드를 부르기 **전에**, 트랜잭션 **밖에서**.
    무엇을 할 것인지 알고 있으므로 미리 적어 두는 것이고, 그러면 어느 갈래로
    끝나든 기록이 빠질 일이 없다.
    """
    Event.record(
        subject=order,
        transition=Event.Transition.ORDER_PAID,
        kind=Event.Kind.ALLOW,
        reason=f'{order.order_number} 을(를) 결제 완료로 옮긴다.',
        before={'status': order.status},
        after={'status': Order.Status.PAID},
    )
    for item in order.items.select_related('product'):
        if item.product is None:
            continue
        Event.record(
            subject=item.product,
            transition=Event.Transition.STOCK_DEDUCTED,
            kind=Event.Kind.ALLOW,
            reason=f'{item.product_name} {item.quantity}개를 차감한다.',
            before={'stock': item.product.stock},
            after={'stock': item.product.stock - item.quantity},
        )
    try:
        order.mark_paid()
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

    다만 **판정보다 먼저** 적는다. 발급 판정이 DENY 로 끝나도 장부에는
    "발급했다" 가 남는다.
    """
    Event.record(
        subject=order,
        transition=Event.Transition.PAYMENT_LINK_ISSUED,
        kind=Event.Kind.ESCALATE,
        rule_ids=[PAY_001],
        reason=f'{order.order_number} 의 결제 링크를 발급한다 — 확정은 고객이 한다.',
        actor=actor,
    )
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
    return (
        Verdict(
            kind=Verdict.ESCALATE,
            rule_ids=[PAY_001],
            reason=(
                f'{PAY_001}: 결제는 고객이 한다. 결제 링크를 발급했다 — '
                f'1시간 안에 고객이 결제해야 한다. {RULE_TEXTS[PAY_001]}'
            ),
            alternatives=['고객에게 링크를 안내하고 결제 완료를 주문 조회로 확인한다'],
        ),
        _payment_payload(order),
    )


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
