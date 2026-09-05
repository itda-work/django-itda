"""에이전트 콘솔 — AI 직원이 앉는 자리.

여기서 AI 직원이 할 수 있는 일은 두 가지다. 주문을 접수하는 것(자동 커밋)과
환불을 제안하는 것(점주 승인 대기). 무엇이 자동이고 무엇이 대기인지는 이 파일이
정하지 않는다 — 계정이 가진 권한이 정한다.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from orders.models import InvalidTransition, Order, OrderItem, Refund
from orders.verdict import Outcome, Verdict
from shop.models import Product

from .scenarios import ALICE_CART, ALICE_SHIPPING, BITS, REFUND_TARGETS, REPAY_TARGET

User = get_user_model()

# 상태 배지 색 — 눈으로 "움직였나?"를 보기 위한 것뿐이다.
ORDER_BADGE = {
    Order.Status.PENDING: 'bg-slate-100 text-slate-700',
    Order.Status.PAID: 'bg-emerald-100 text-emerald-800',
    Order.Status.SHIPPING: 'bg-sky-100 text-sky-800',
    Order.Status.COMPLETED: 'bg-sky-100 text-sky-800',
    Order.Status.CANCELLED: 'bg-rose-100 text-rose-800',
}
REFUND_BADGE = {
    Refund.Status.PROPOSED: 'bg-amber-100 text-amber-800',
    Refund.Status.APPROVED: 'bg-emerald-100 text-emerald-800',
    Refund.Status.REJECTED: 'bg-rose-100 text-rose-800',
}


@login_required
def console(request):
    watched_numbers = [number for number, _ in REFUND_TARGETS.values()]
    watched = [
        {'order': order, 'badge': ORDER_BADGE.get(order.status, 'bg-slate-100 text-slate-700')}
        for order in Order.objects.filter(order_number__in=watched_numbers).order_by('order_number')
    ]
    refunds = [
        {'refund': refund, 'badge': REFUND_BADGE.get(refund.status, 'bg-slate-100')}
        for refund in Refund.objects.select_related('order', 'requested_by', 'decided_by')
    ]
    recent_orders = [
        {'order': order, 'badge': ORDER_BADGE.get(order.status, 'bg-slate-100 text-slate-700')}
        for order in Order.objects.order_by('-pk')[:5]
    ]
    # '직접 승인 시도' 버튼이 겨냥할 제안. 없으면 버튼을 숨긴다.
    self_target = Refund.objects.filter(status=Refund.Status.PROPOSED).order_by('-pk').first()

    return render(
        request,
        'agent/console.html',
        {
            'bits': BITS,
            'watched': watched,
            'refunds': refunds,
            'recent_orders': recent_orders,
            'self_target': self_target,
        },
    )


@require_POST
@login_required
def act(request, bit):
    """고정 시나리오 한 비트를 실행한다."""
    if bit == 'order-intake':
        _require(request, 'orders.add_order')
        order = _intake_order(request.user)
        messages.success(
            request,
            f'세계가 받아들였다 ✅ — 주문 {order.order_number} 이(가) '
            f'{order.get_status_display()} 상태로 생겼습니다. 아무도 승인하지 않았습니다.',
        )
    elif bit in REFUND_TARGETS:
        _require(request, 'orders.add_refund')
        verdict, outcome = _propose_refund(request.user, bit)
        refund = outcome.refund
        if outcome.state == Outcome.ALREADY:
            messages.info(request, f'{verdict.reason}')
        elif outcome.state == Outcome.COMMITTED:
            messages.success(
                request,
                f'규칙이 확정했다 ✅ — {refund.order.order_number} 환불 '
                f'{refund.amount:,}원을 자동 승인했습니다. {verdict.reason}',
            )
        elif outcome.state == Outcome.QUEUED:
            messages.warning(
                request,
                f'점주 승인 대기 ⏳ — {verdict.reason} '
                f'주문 상태는 아직 {refund.order.get_status_display()} 입니다.',
            )
        return _respond(request, verdict)
    elif bit == 'repay-paid':
        _require(request, 'orders.change_order')
        verdict = _repay(request.user)
        return _respond(request, verdict)
    else:
        raise PermissionDenied('알 수 없는 시나리오입니다.')

    return _respond(request, Verdict(kind=Verdict.ALLOW))


def _require(request, perm):
    """권한이 없으면 여기서 끝난다. 화면에서 버튼을 숨기는 것과는 다른 일이다."""
    if not request.user.has_perm(perm):
        raise PermissionDenied(f'{perm} 권한이 없습니다.')


@transaction.atomic
def _intake_order(actor):
    alice = get_object_or_404(User, username='alice')
    lines = [(Product.objects.get(name=name), quantity) for name, quantity in ALICE_CART]
    order = Order.objects.create(
        user=alice,
        status=Order.Status.PENDING,
        total_amount=sum(product.price * quantity for product, quantity in lines),
        **ALICE_SHIPPING,
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


def _propose_refund(actor, bit):
    number, reason = REFUND_TARGETS[bit]
    order = get_object_or_404(Order, order_number=number)
    existing = (
        Refund.objects.filter(order=order).exclude(status=Refund.Status.REJECTED).first()
    )
    if existing:
        # 같은 제안을 두 번 올려도 큐가 지저분해지지 않게 콘솔 쪽에서만 걸러 준다.
        # 세계가 막아 주는 것이 아니다 — 그 얘기는 5단계다.
        # 다시 판정하지 않고 **지금 처리 상태**를 그대로 답한다.
        return existing.current_outcome()
    return Refund.apply(
        order=order,
        amount=order.total_amount,
        reason=reason,
        requested_by=actor,
    )


def _repay(actor):
    """이미 결제 완료된 주문에 결제 처리를 한 번 더 돌린다.

    권한 검사는 통과했다 — 결제 처리는 AI 직원의 업무다.
    그런데 '지금 상태에서 갈 수 있는 곳인가'는 권한이 답하지 않는다. 전이 계약이 답한다.
    """
    order = get_object_or_404(Order, order_number=REPAY_TARGET)
    try:
        order.mark_paid()
    except InvalidTransition as denied:
        return denied.verdict
    return Verdict(
        kind=Verdict.ALLOW,
        reason=f'{order.order_number} 을(를) 결제 완료로 옮겼습니다.',
    )


def _respond(request, verdict):
    """판정을 밖으로 내보낸다 — 같은 판정, 두 가지 표현.

    API(`Accept: application/json`)에는 판정 객체를 그대로 준다. 실접속 트랙의
    MCP 클라이언트가 읽을 형식이 이것이다.
    브라우저에는 ALLOW·ESCALATE 는 콘솔로 되돌리고(PRG), DENY 만 409 화면을 띄운다.
    """
    if 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse(
            verdict.as_dict(),
            status=verdict.status_code,
            json_dumps_params={'ensure_ascii': False},
        )
    if verdict.kind == Verdict.DENY:
        return render(request, 'agent/denied.html', {'verdict': verdict}, status=409)
    return redirect('agent:console')
