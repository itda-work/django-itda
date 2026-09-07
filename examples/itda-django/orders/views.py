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
def pay_page(request, pk):
    """결제 페이지 — 링크를 받은 사람이 자기 손으로 확정하는 자리.

    이 화면은 세 가지를 물어야 한다.

    1. **누구인가** — 로그인이 답한다(`login_required`).
    2. **누구의 것인가** — 아직 아무도 안 묻는다. 아래 조회에는 `pk` 뿐이다.
       로그인만 하면 남의 주문이 열리고, 남의 주소·전화·금액이 보이고,
       결제 버튼까지 눌린다.
    3. **언제까지인가** — 링크에 시간이 없다. URL 은 복사되고 전달되고 남는다.
       어제 준 링크가 오늘도 세계를 움직인다.

    2·3 이 6단계에서 켜는 법 둘이다. 지금은 둘 다 없다.
    """
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        verdict = services.pay_order(order)
        order.refresh_from_db()
        return render(
            request,
            'orders/pay_result.html',
            {'verdict': verdict, 'order': order},
            status=verdict.status_code,
        )
    return render(request, 'orders/pay.html', {'order': order, 'expires_at': None})
