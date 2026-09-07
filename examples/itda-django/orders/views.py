"""사람이 확정하는 자리 둘 — 점주의 승인과 고객의 결제.

**환불 확정(점주)** 은 `orders.change_refund` 권한이 있는 계정만 호출할 수 있다.
그 검사는 데코레이터 한 줄이 전부다. AI 직원이 호출하면 여기서 403으로 끊긴다.

**결제(고객)** 는 6단계에서 생긴다. 결제는 돈을 내는 사람의 확정이라 AI 직원이
대신 누를 일이 아니다 — AI 의 문에서는 링크 발급까지고(`services.issue_payment_link`),
`Order.mark_paid` 를 부르는 곳은 이 아래의 결제 페이지 하나다.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .models import InvalidTransition, Order, Refund
from .rules import PAY_001, RULE_TEXTS
from .tokens import payment_token
from .verdict import Verdict


def _next_url(request):
    """되돌아갈 곳. 외부 주소로 튕기지 않도록 내부 경로만 허용한다."""
    target = request.POST.get('next', '')
    if target.startswith('/') and not target.startswith('//'):
        return target
    return '/agent/'


@require_POST
@login_required
@permission_required('orders.change_refund', raise_exception=True)
def approve_refund(request, pk):
    refund = get_object_or_404(Refund, pk=pk)
    try:
        refund.approve(request.user)
    except InvalidTransition as blocked:
        # 여기는 사람이 보는 화면이다. 409 화면은 AI 직원이 읽을 판정용이고,
        # 버튼을 두 번 누른 점주에게 필요한 것은 "이미 지나간 일"이라는 한 줄이다.
        messages.warning(request, blocked.verdict.reason)
        return redirect(_next_url(request))
    messages.success(
        request, f'{refund.order.order_number} 환불을 승인했습니다. 주문이 취소로 바뀌었습니다.'
    )
    return redirect(_next_url(request))


@require_POST
@login_required
@permission_required('orders.change_refund', raise_exception=True)
def reject_refund(request, pk):
    refund = get_object_or_404(Refund, pk=pk)
    try:
        refund.reject(request.user, note=request.POST.get('note', ''))
    except InvalidTransition as blocked:
        messages.warning(request, blocked.verdict.reason)
        return redirect(_next_url(request))
    messages.info(
        request, f'{refund.order.order_number} 환불을 거부했습니다. 주문 상태는 그대로입니다.'
    )
    return redirect(_next_url(request))


# --- 고객의 자리 — 결제 페이지 -------------------------------------------------


@login_required(login_url='accounts:login')
def pay_page(request, pk, token):
    """결제 페이지 — 링크를 받은 사람이 자기 손으로 확정하는 자리.

    세 질문을 **순서대로** 묻는다.

    1. **누구인가** — 로그인이 답한다(`login_required`).
    2. **누구의 것인가** — `user=request.user` 한 인자다(SCOPE-001@v1). 남의
       주문은 **404**다. 403 이 아니다 — "있는데 못 본다"가 아니라 "네 세계에는
       없다"이고, 남의 주문이 있다는 사실 자체가 정보이기 때문이다.
    3. **언제인가** — 서명 토큰이 답한다(PAY-001@v1). 1시간이 지났거나 주문
       상태가 바뀌었으면 같은 토큰이 스스로 죽는다.

    **만료는 이중 결제를 막는 장치가 아니다.** 그건 4단계 `ORDER-001@v1` 의
    몫이고 그대로다. POST 는 1·2 를 다시 지난 뒤 `services.pay_order` 를 부르고,
    전이 계약이 마지막에 지킨다 — 토큰이 먼저 닫고 계약이 마지막에 지키는 두 겹이다.
    (두 탭에서 동시에 누르면 토큰은 둘 다 통과하고 계약이 하나를 거른다.)
    """
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if not payment_token.check_token(order, token):
        return _link_closed(request, order)
    if request.method == 'POST':
        verdict = services.pay_order(order)
        order.refresh_from_db()
        return render(
            request,
            'orders/pay_result.html',
            {'verdict': verdict, 'order': order},
            status=verdict.status_code,
        )
    return render(
        request,
        'orders/pay.html',
        {'order': order, 'expires_at': payment_token.expires_at(token)},
    )


def _link_closed(request, order):
    """토큰이 안 맞는다 — 왜인지 **세지 않는다.**

    시간이 지난 것과 상태가 바뀐 것과 위조된 것을 하나의 답으로 묶는다. 서명
    검사는 셋을 구분해 주지 않고(해시가 다르면 다를 뿐이다), 구분해 주는 척하면
    거짓말이다. 그래서 사유 문장이 "1시간이 지났거나 주문 상태가 바뀌었다"이다.

    대신 **대안은 상태를 보고 고른다.** 없는 길을 적지 않는다는 4단계 규율 그대로다 —
    이미 결제된 주문에 "새 링크를 요청하라"는 대안이 아니라 헛걸음이다.
    """
    alternatives = (
        ['AI 직원에게 새 결제 링크를 요청한다']
        if order.status == Order.Status.PENDING
        else ['주문 내역에서 현재 상태를 확인한다']
    )
    verdict = Verdict(
        kind=Verdict.DENY,
        rule_ids=[PAY_001],
        reason=(
            f'{PAY_001}: 결제 링크가 닫혔다 — 1시간이 지났거나 주문 상태가 바뀌었다. '
            f'{RULE_TEXTS[PAY_001]}'
        ),
        alternatives=alternatives,
    )
    return render(
        request, 'orders/pay_result.html', {'verdict': verdict, 'order': order}, status=409
    )
