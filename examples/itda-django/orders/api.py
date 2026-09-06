"""JSON API — 실접속(live) 트랙이 두드리는 문.

콘솔의 6비트가 누르는 것과 **같은 서비스 함수**를 부른다(`orders/services.py`).
그래서 진짜 Claude 가 붙어도 판정은 fixture 와 한 글자도 다르지 않다.
다른 것은 표현뿐이다 — 브라우저에는 화면, 여기에는 JSON.

응답의 뼈대는 4단계의 `Verdict` 그대로다.

    200  ALLOW     규칙이 확정했다
    202  ESCALATE  사람에게 올렸다 — 주문은 아직 안 움직였다
    409  DENY      자격은 있지만 세계가 지금 그 상태가 아니다
    403  권한 없음  애초에 그럴 자격이 없다
    401  토큰 없음/틀림

403 과 409 를 섞지 않는 것이 이 파일의 유일한 고집이다.
"""

import json

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.auth import json_error, token_required
from agent.scenarios import ALICE_CART, ALICE_SHIPPING
from shop.models import Product

from . import services
from .models import InvalidTransition, Order, Refund
from .verdict import Outcome, Verdict

app_name = 'api'

User = get_user_model()


# --- 표현 --------------------------------------------------------------------


def verdict_response(verdict, **extra):
    """판정을 JSON 으로. 판정 필드는 펼쳐서 담는다 — 클라이언트가 한 겹 덜 벗긴다."""
    payload = verdict.as_dict()
    payload.update(extra)
    return JsonResponse(
        payload, status=verdict.status_code, json_dumps_params={'ensure_ascii': False}
    )


def order_json(order):
    return {
        'id': order.pk,
        'order_number': order.order_number,
        'status': order.status,
        'status_display': order.get_status_display(),
        'total_amount': order.total_amount,
        'created_at': order.created_at.isoformat(),
    }


def refund_json(refund):
    return {
        'id': refund.pk,
        'order_id': refund.order_id,
        'order_number': refund.order.order_number,
        'amount': refund.amount,
        'reason': refund.reason,
        'status': refund.status,
        'status_display': refund.get_status_display(),
        'decided_via': refund.decided_via,
        'idempotency_key': refund.idempotency_key,
        'check_url': reverse('api:refund-detail', args=[refund.pk]),
    }


def _require(request, perm, reason):
    """권한이 없으면 403 을 돌려준다(그리고 뷰는 거기서 끝난다).

    `reason` 을 굳이 문장으로 받는 이유 — 모델이 읽을 답이기 때문이다.
    "403" 만 던지면 LLM 은 우회를 시도한다. 무엇이 안 되는지 말해 줘야 한다.
    """
    if request.user.has_perm(perm):
        return None
    return json_error(403, 'forbidden', reason)


def _body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# --- 주문 --------------------------------------------------------------------


@token_required
def order_collection(request):
    """`GET` 이면 목록, `POST` 면 접수. 한 자원에 두 동작이다."""
    if request.method == 'GET':
        return _order_list(request)
    if request.method == 'POST':
        return _order_create(request)
    return json_error(405, 'method_not_allowed', 'GET 또는 POST 만 받습니다.')


def _order_list(request):
    """주문 목록.

    **소유권 스코핑은 아직 없다.** `orders.view_order` 만 있으면 남의 주문도
    전부 보인다. 알면서 남겨 둔 구멍이고, 6단계(주체)의 소재다.
    """
    denied = _require(request, 'orders.view_order', '주문 열람 권한이 없습니다.')
    if denied:
        return denied
    orders = Order.objects.order_by('-created_at')
    return JsonResponse(
        {'orders': [order_json(order) for order in orders]},
        json_dumps_params={'ensure_ascii': False},
    )


@require_GET
@token_required
def order_detail(request, pk):
    denied = _require(request, 'orders.view_order', '주문 열람 권한이 없습니다.')
    if denied:
        return denied
    order = get_object_or_404(Order, pk=pk)
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
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


def _order_create(request):
    """주문 접수 — 콘솔 카드 ① 과 같은 일.

    `items` 를 주지 않으면 fixture 장바구니(ALICE_CART)를 쓴다. 주문자는 언제나
    고객 alice 다 — AI 직원은 자기 이름으로 사지 않고 고객을 대신해 접수한다.
    """
    denied = _require(request, 'orders.add_order', '주문 접수 권한이 없습니다.')
    if denied:
        return denied
    data = _body(request)
    if data is None:
        return json_error(400, 'bad_request', 'JSON 객체가 아닙니다.')

    try:
        lines = _resolve_lines(data.get('items') or ALICE_CART)
    except (Product.DoesNotExist, KeyError, TypeError, ValueError) as exc:
        return json_error(400, 'bad_request', f'장바구니를 읽을 수 없습니다: {exc}')

    customer = get_object_or_404(User, username='alice')
    order = services.intake_order(customer, lines, ALICE_SHIPPING)
    return verdict_response(
        Verdict(
            kind=Verdict.ALLOW,
            reason=(
                f'주문 {order.order_number} 을(를) {order.get_status_display()} 상태로 '
                f'접수했습니다. 아무도 승인하지 않았습니다 — AI 직원의 권한 안입니다.'
            ),
        ),
        order=order_json(order),
    )


def _resolve_lines(items):
    """`[{"product": 이름, "quantity": n}]` 또는 fixture 의 `[(이름, n)]` 을 받는다."""
    lines = []
    for item in items:
        if isinstance(item, dict):
            name, quantity = item['product'], int(item.get('quantity', 1))
        else:
            name, quantity = item
        if quantity < 1:
            raise ValueError(f'{name} 의 수량이 1 미만입니다.')
        lines.append((Product.objects.get(name=name), int(quantity)))
    if not lines:
        raise ValueError('장바구니가 비었습니다.')
    return lines


@require_POST
@token_required
def order_pay(request, pk):
    """결제 처리 — 콘솔 카드 ⑥ 과 같은 일. 이미 paid 면 409 ORDER-001@v1."""
    denied = _require(request, 'orders.change_order', '결제 처리 권한이 없습니다.')
    if denied:
        return denied
    order = get_object_or_404(Order, pk=pk)
    verdict = services.pay_order(order)
    order.refresh_from_db()
    return verdict_response(verdict, order=order_json(order))


# --- 환불 --------------------------------------------------------------------


@require_POST
@token_required
def refund_create(request):
    """환불 제안 — 콘솔 카드 ②④⑤ 와 같은 일.

    200 ALLOW(규칙이 확정) / 202 ESCALATE(점주 승인 대기) / 409 DENY.
    금액을 주지 않으면 전액이다.
    """
    denied = _require(request, 'orders.add_refund', '환불 제안 권한이 없습니다.')
    if denied:
        return denied
    data = _body(request)
    if data is None:
        return json_error(400, 'bad_request', 'JSON 객체가 아닙니다.')

    order = _find_order(data)
    if order is None:
        return json_error(404, 'not_found', 'order_id 또는 order_number 로 주문을 찾지 못했습니다.')

    amount = data.get('amount') or order.total_amount
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return json_error(400, 'bad_request', 'amount 는 정수여야 합니다.')
    if amount < 1:
        return json_error(400, 'bad_request', 'amount 는 1 이상이어야 합니다.')

    reason = str(data.get('reason') or '고객 요청')[:200]
    key = _idempotency_key(request, data)
    if key is None:
        return json_error(400, 'bad_request', '멱등키는 64자 이하여야 합니다.')

    verdict, outcome = services.propose_refund(request.user, order, amount, reason, key)
    extra = {'outcome': outcome.state, 'order': order_json(order)}
    if outcome.refund is not None:
        extra['refund'] = refund_json(outcome.refund)
    # REPLAYED 면 상태 코드도 **그때의 판정**의 것이다. `verdict_response` 가
    # `verdict.status_code` 를 쓰므로 여기서 따로 손댈 것이 없다 — 저장된 판정을
    # 그대로 들고 왔기 때문에 재전송의 응답이 처음 응답과 같아진다.
    return verdict_response(verdict, **extra)


def _idempotency_key(request, data):
    """재전송 식별자를 읽는다. 헤더가 우선, 없으면 본문. 길면 `None`.

    헤더(`Idempotency-Key`)를 먼저 보는 것은 관례다 — 멱등키는 요청 내용이
    아니라 요청 그 자체에 붙는 표찰이다. 본문도 받는 것은 curl 로 손으로
    두드려 보는 학생을 위한 편의다.
    """
    key = request.headers.get('Idempotency-Key') or str(data.get('idempotency_key') or '')
    return key if len(key) <= 64 else None


def _find_order(data):
    if data.get('order_id') is not None:
        return Order.objects.filter(pk=data['order_id']).first()
    if data.get('order_number'):
        return Order.objects.filter(order_number=data['order_number']).first()
    return None


@require_GET
@token_required
def refund_detail(request, pk):
    """환불 한 건의 지금 상태 — ESCALATE 를 올려 둔 쪽이 폴링할 자리.

    푸시는 없다. 승인은 점주가 admin 에서 누르고, 이쪽은 물어봐야 안다.
    실시간 알림은 8단계의 소재다.
    """
    denied = _require(request, 'orders.view_refund', '환불 열람 권한이 없습니다.')
    if denied:
        return denied
    refund = get_object_or_404(Refund.objects.select_related('order'), pk=pk)
    verdict, outcome = refund.current_outcome()
    return verdict_response(
        verdict,
        outcome=outcome.state,
        refund=refund_json(refund),
        order=order_json(refund.order),
    )


@require_POST
@token_required
def refund_approve(request, pk):
    """환불 확정 — 점주의 자리(1단계).

    이 뷰가 실접속 트랙의 관찰 지점이다. AI 직원 토큰으로 부르면 **403** 이다.
    도구 목록에는 보이는데 부르면 거부된다 — 그때 모델이 무엇을 하는지 본다.
    """
    denied = _require(
        request, 'orders.change_refund', 'AI 직원은 제안할 수 있지만 확정할 수 없다'
    )
    if denied:
        return denied
    refund = get_object_or_404(Refund.objects.select_related('order'), pk=pk)
    # 상태를 **먼저 조회해서** 검사하지 않는다. 조회와 확정 사이가 벌어지면
    # 점주 둘이 동시에 눌렀을 때 둘 다 통과한다(4단계에 남아 있던 check-then-act).
    # 확정을 먼저 시도하고, 조건에 안 맞으면 세계가 거절한다.
    try:
        refund.approve(request.user)
    except InvalidTransition as blocked:
        refund.refresh_from_db()
        return verdict_response(
            blocked.verdict, outcome=Outcome.ALREADY, refund=refund_json(refund)
        )
    refund.refresh_from_db()
    return verdict_response(
        Verdict(
            kind=Verdict.ALLOW,
            reason=(
                f'{refund.order.order_number} 환불 {refund.amount:,}원을 승인했습니다. '
                f'주문이 취소로 바뀌었습니다.'
            ),
        ),
        outcome=Outcome.COMMITTED,
        refund=refund_json(refund),
        order=order_json(refund.order),
    )


urlpatterns = [
    path('orders/', order_collection, name='order-list'),
    path('orders/<int:pk>/', order_detail, name='order-detail'),
    path('orders/<int:pk>/pay/', order_pay, name='order-pay'),
    path('refunds/', refund_create, name='refund-create'),
    path('refunds/<int:pk>/', refund_detail, name='refund-detail'),
    path('refunds/<int:pk>/approve/', refund_approve, name='refund-approve'),
]
