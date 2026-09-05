"""환불 확정 API — 점주의 자리.

승인·거부는 `orders.change_refund` 권한이 있는 계정만 호출할 수 있다.
그 검사는 데코레이터 한 줄이 전부다. AI 직원이 호출하면 여기서 403으로 끊긴다.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .models import Refund


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
    refund.approve(request.user)
    messages.success(
        request, f'{refund.order.order_number} 환불을 승인했습니다. 주문이 취소로 바뀌었습니다.'
    )
    return redirect(_next_url(request))


@require_POST
@login_required
@permission_required('orders.change_refund', raise_exception=True)
def reject_refund(request, pk):
    refund = get_object_or_404(Refund, pk=pk)
    refund.reject(request.user, note=request.POST.get('note', ''))
    messages.info(
        request, f'{refund.order.order_number} 환불을 거부했습니다. 주문 상태는 그대로입니다.'
    )
    return redirect(_next_url(request))
