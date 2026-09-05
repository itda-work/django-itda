"""4단계 — 규칙의 이사: 프롬프트에 있던 도메인 법을 코드로 옮겼는가.

실측 대상은 "LLM 이 속는 확률" 이 아니라 **보장 부재**다.
고정된 악성 응답이 서버를 호출했을 때 서버가 그냥 허용하는가, 아니면
세계가 판정해서 끊는가. 시작 상태(`stage-04-start`)에서는 12개 중 11개가 실패한다
(하나만 통과한다 — 계약은 정상 호출까지 막는 것이 아니기 때문이다).

판정은 세 값이다 — ALLOW / DENY / ESCALATE. 그리고 DENY·ESCALATE 에는
**대안(alternatives)** 이 붙는다. 거절만 하고 길을 안 알려주면 AI 직원은 우회를 시도한다.
"""

import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import OperationalError, connection
from django.utils import timezone

from orders.models import Order, OrderItem, Refund
from shop.models import Product

User = get_user_model()

LATE_URL = '/agent/act/refund-late/'
REPAY_URL = '/agent/act/repay-paid/'

# 시작 상태에는 아직 없는 모듈들이다. 여기서 죽으면 파일 전체가 수집조차 안 되므로
# (그러면 '무엇이 몇 개 실패했는지'가 안 보인다) 지연 import 로 잡아 둔다.
try:
    from orders.rules import (
        ESCALATE_OVER,
        ORDER_001,
        REFUND_001,
        REFUND_002,
        REFUND_WINDOW_DAYS,
    )
    from orders.verdict import Verdict

    MISSING = None
except ImportError as exc:  # 4단계 시작 상태
    ESCALATE_OVER, REFUND_WINDOW_DAYS = 50_000, 7
    ORDER_001, REFUND_001, REFUND_002 = 'ORDER-001@v1', 'REFUND-001@v1', 'REFUND-002@v1'
    Verdict = None
    MISSING = str(exc)


@pytest.fixture
def contract():
    """판정 계약이 갖춰졌는지 확인한다. 없으면 그 자리에서 실패한다."""
    if MISSING:
        pytest.fail(f'판정 계약이 아직 없다 — {MISSING}. 4단계에서 이사시킨다.')
    from orders.models import InvalidTransition

    return InvalidTransition


def _order(days_ago, total, status=Order.Status.PAID, number=None, product_name=None, quantity=1):
    """며칠 전에 결제된 주문 하나를 만든다."""
    alice = User.objects.get(username='alice')
    order = Order.objects.create(
        order_number=number or f'T-{days_ago}-{total}',
        user=alice,
        status=status,
        recipient_name='김앨리스',
        phone='010-0000-0001',
        address='서울시 가상구 없는동 1-1',
        total_amount=total,
    )
    if product_name:
        product = Product.objects.get(name=product_name)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
        )
    Order.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )
    order.refresh_from_db()
    return order


# --- 판정 계약 -----------------------------------------------------------------


@pytest.mark.django_db
def test_판정은_세_값과_대안을_가진다(world, contract):
    """Verdict 는 참/거짓이 아니다. 왜 그렇게 판정했는지(rule_ids)까지 함께 나온다."""
    verdict = Refund.decide(_order(3, 30_000), 30_000)

    assert isinstance(verdict, Verdict)
    assert verdict.kind in (Verdict.ALLOW, Verdict.DENY, Verdict.ESCALATE)
    assert isinstance(verdict.rule_ids, list)
    assert isinstance(verdict.alternatives, list)


@pytest.mark.django_db
def test_결제_후_7일이_지난_환불은_DENY(world, contract):
    """REFUND-001@v1 — 프롬프트 1번 문장이 코드로 이사 왔는가."""
    order = _order(REFUND_WINDOW_DAYS + 1, 22_500)

    verdict = Refund.decide(order, 22_500)

    assert verdict.kind == Verdict.DENY
    assert REFUND_001 in verdict.rule_ids
    assert verdict.alternatives, 'DENY 에도 대안이 있어야 한다 — 막기만 하면 우회를 시도한다.'


@pytest.mark.django_db
def test_DENY_판정이면_환불이_커밋되지_않는다(world, contract):
    order = _order(REFUND_WINDOW_DAYS + 1, 22_500)

    ai = User.objects.get(username='ai-staff')

    verdict, refund = Refund.apply(order, 22_500, '늦은 요청', ai)

    assert verdict.kind == Verdict.DENY
    assert refund is None or refund.status == Refund.Status.REJECTED
    order.refresh_from_db()
    assert order.status != Order.Status.CANCELLED


@pytest.mark.django_db
def test_기한_안_소액은_ALLOW_자동_승인(world, contract):
    """3일 지난 3만원 — 임계 아래다. 사람을 부르지 않는다."""
    order = _order(3, 30_000)

    ai = User.objects.get(username='ai-staff')

    verdict, refund = Refund.apply(order, 30_000, '사이즈 불일치', ai)

    assert verdict.kind == Verdict.ALLOW
    assert refund.status == Refund.Status.APPROVED
    assert refund.decided_by is None, '규칙이 확정한 것이지 사람이 확정한 것이 아니다.'
    assert refund.decided_via == 'rule'


@pytest.mark.django_db
def test_기한_안_고액은_ESCALATE(world, contract):
    """3일 지난 7만원 — REFUND-002@v1. 점주에게 올라가고, 대안이 함께 나온다."""
    order = _order(3, 70_000)

    ai = User.objects.get(username='ai-staff')

    verdict, refund = Refund.apply(order, 70_000, '사이즈 불일치', ai)

    assert verdict.kind == Verdict.ESCALATE
    assert REFUND_002 in verdict.rule_ids
    assert refund.status == Refund.Status.PROPOSED
    assert refund.decided_by is None
    assert any(str(ESCALATE_OVER // 10000) in a or '분할' in a for a in verdict.alternatives), (
        f'대안에 분할 환불 같은 길이 하나는 있어야 한다: {verdict.alternatives}'
    )


# --- 전이 계약 -----------------------------------------------------------------


@pytest.mark.django_db
def test_pending_에서만_paid_로_간다(world, contract):
    """ORDER-001@v1 — 이미 paid 인 주문에 mark_paid() 를 또 부르면 rowcount 가 0이다."""
    InvalidTransition = contract
    order = Order.objects.get(order_number='SEED-0002')
    assert order.status == Order.Status.PAID
    product = order.items.select_related('product').first().product
    before = product.stock

    with pytest.raises(InvalidTransition) as caught:
        order.mark_paid()

    assert ORDER_001 in caught.value.verdict.rule_ids
    assert Product.objects.get(pk=product.pk).stock == before, '재고는 건드리지 않았어야 한다.'
    order.refresh_from_db()
    assert order.status == Order.Status.PAID


@pytest.mark.django_db
def test_정상_전이는_그대로_통과한다(world):
    """계약은 모든 호출을 막는 것이 아니다. 계약을 어기는 호출만 막는다."""
    order = _order(0, 18_000, status=Order.Status.PENDING, product_name='만년필 잉크 30ml')
    product = Product.objects.get(name='만년필 잉크 30ml')
    before = product.stock

    order.mark_paid()

    order.refresh_from_db()
    assert order.status == Order.Status.PAID
    assert Product.objects.get(pk=product.pk).stock == before - 1


@pytest.mark.django_db(transaction=True)
def test_동시에_두_번_결제해도_한_번만_성공한다(world, contract):
    """조건부 UPDATE 의 rowcount 는 경합에서도 정확히 하나만 1이다.

    5단계(경합)의 예고편이다. 여기서는 전이 계약 하나만으로도
    '두 번 깎이는 일'이 사라진다는 것까지만 본다.
    """
    InvalidTransition = contract
    order = _order(0, 18_000, status=Order.Status.PENDING, product_name='만년필 잉크 30ml')
    before = Product.objects.get(name='만년필 잉크 30ml').stock
    results = []

    def run():
        try:
            Order.objects.get(pk=order.pk).mark_paid()
            results.append('ok')
        except InvalidTransition:
            results.append('denied')
        except OperationalError:
            # SQLite 는 쓰기 잠금이 파일 단위다. 잠금으로 밀린 쪽도 '성공하지 못했다'.
            results.append('locked')
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count('ok') == 1, f'정확히 한 번만 성공해야 한다: {results}'
    assert Product.objects.get(name='만년필 잉크 30ml').stock == before - 1


# --- 콘솔 악성 카드 ⑤⑥ -------------------------------------------------------


@pytest.mark.django_db
def test_카드5_8일_지난_환불은_409로_끊긴다(world, client):
    assert client.login(username='ai-staff', password='ai1234')

    response = client.post(LATE_URL, HTTP_ACCEPT='application/json')

    assert response.status_code == 409
    body = response.json()
    assert body['kind'] == 'DENY'
    assert REFUND_001 in body['rule_ids']
    assert body['alternatives']
    assert Order.objects.get(order_number='SEED-0001').status != Order.Status.CANCELLED


@pytest.mark.django_db
def test_카드6_재결제는_409로_끊기고_재고가_안_깎인다(world, client):
    assert client.login(username='ai-staff', password='ai1234')
    before = Product.objects.get(name='만년필 잉크 30ml').stock

    response = client.post(REPAY_URL, HTTP_ACCEPT='application/json')

    assert response.status_code == 409
    body = response.json()
    assert ORDER_001 in body['rule_ids']
    assert Product.objects.get(name='만년필 잉크 30ml').stock == before


@pytest.mark.django_db
def test_카드2_고액_제안은_202로_점주에게_올라간다(world, client):
    """1단계에서 본 격상이 이제 판정 값으로 표현된다."""
    assert client.login(username='ai-staff', password='ai1234')

    response = client.post('/agent/act/refund-size/', HTTP_ACCEPT='application/json')

    assert response.status_code == 202
    body = response.json()
    assert body['kind'] == 'ESCALATE'
    assert REFUND_002 in body['rule_ids']
    assert Refund.objects.get(order__order_number='SEED-0002').status == Refund.Status.PROPOSED


# --- 규칙 대장 -----------------------------------------------------------------


def test_규칙_대장에_세_행이_있다():
    """이사한 규칙은 대장에 남는다. 코드만 고치고 대장을 안 쓰면 반쪽이다."""
    from pathlib import Path

    ledger = (Path(__file__).resolve().parent.parent / 'RULES.md').read_text(encoding='utf-8')

    for rule_id in (REFUND_001, REFUND_002, ORDER_001):
        assert rule_id in ledger, f'{rule_id} 이(가) RULES.md 에 없다.'
