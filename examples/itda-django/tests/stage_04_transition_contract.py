"""4단계 — 규칙의 이사: 프롬프트에 있던 도메인 법을 코드로 옮겼는가.

실측 대상은 "LLM 이 속는 확률" 이 아니라 **보장 부재**다.
고정된 악성 응답이 서버를 호출했을 때 서버가 그냥 허용하는가, 아니면
세계가 판정해서 끊는가. 시작 상태(`stage-04-start`)에서는 12개 중 11개가 실패한다
(하나만 통과한다 — 계약은 정상 호출까지 막는 것이 아니기 때문이다).

판정은 세 값이다 — ALLOW / DENY / ESCALATE. 그리고 DENY·ESCALATE 에는
**대안(alternatives)** 이 붙는다. 거절만 하고 길을 안 알려주면 AI 직원은 우회를 시도한다.

## 사후 변경(7단계)

두 곳이 바뀌었다. 재는 것은 그대로다 — 전이 계약이 조건 불일치를 거부하는가.

- `Order.mark_paid()` → **`mark_paid(actor)`**. 누가 결제했는지를 전이가 받는다
  (장부의 자리 칸). 이 파일은 `order.user` 를 넘긴다.
- `_order()` fixture 가 결제 완료 이후 상태의 주문에 `paid_at` 을 채운다
  (`order_paid_has_paid_at`). 채점표가 만드는 세계도 세계의 제약 안에 있어야 한다.

**태그 `stage-04-*` 는 이동하지 않는다** — 옛 태그를 checkout 하면 옛 시그니처로
도는 옛 테스트가 그대로 있다.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError
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
        RULE_TEXTS,
    )
    from orders.verdict import Outcome, Verdict

    MISSING = None
except ImportError as exc:  # 4단계 시작 상태
    ESCALATE_OVER, REFUND_WINDOW_DAYS = 50_000, 7
    ORDER_001, REFUND_001, REFUND_002 = 'ORDER-001@v1', 'REFUND-001@v1', 'REFUND-002@v1'
    RULE_TEXTS = {
        REFUND_001: '결제 후 7일이 지난 주문은 환불하지 마라.',
        REFUND_002: '5만원을 초과하는 환불은 반드시 점주 승인을 받아라.',
        ORDER_001: '이미 결제 완료된 주문을 다시 결제 완료로 만들지 마라.',
    }
    Outcome = Verdict = None
    MISSING = str(exc)

# 대안 목록에 있으면 안 되는 말 — 이 세계에 경로가 없는 약속들이다.
# (승인 회피를 권하는 '분할'과 미구현 '예외 승인·교환·재발송')
FORBIDDEN_ALTERNATIVES = ('분할', '예외 승인', '교환', '재발송')


@pytest.fixture
def contract():
    """판정 계약이 갖춰졌는지 확인한다. 없으면 그 자리에서 실패한다."""
    if MISSING:
        pytest.fail(f'판정 계약이 아직 없다 — {MISSING}. 4단계에서 이사시킨다.')
    from orders.models import InvalidTransition

    return InvalidTransition


def _order(
    days_ago,
    total,
    status=Order.Status.PAID,
    number=None,
    product_name=None,
    quantity=1,
    age=None,
):
    """며칠 전에 결제된 주문 하나를 만든다.

    `age` 에 timedelta 를 주면 시간 단위까지 정확히 늙힐 수 있다(경계 검사용).
    """
    alice = User.objects.get(username='alice')
    order = Order.objects.create(
        order_number=number or f'T-{days_ago}-{total}',
        user=alice,
        status=status,
        recipient_name='김앨리스',
        phone='010-0000-0001',
        address='서울시 가상구 없는동 1-1',
        total_amount=total,
        # 사후 변경(7단계) — 결제 완료 이후 상태의 주문은 결제 시각을 가진다
        # (`order_paid_has_paid_at`). 이 파일이 만드는 fixture 도 세계의 제약
        # 안에 있어야 한다. 값은 backfill 과 같은 규칙(결제일 = 주문일)이다.
        paid_at=None if status == Order.Status.PENDING else timezone.now(),
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
        created_at=timezone.now() - (age if age is not None else timedelta(days=days_ago))
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
    _alternatives_are_real(verdict)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('label', 'age', 'expected'),
    [
        ('7일 1시간 전', timedelta(days=REFUND_WINDOW_DAYS, hours=1), 'DENY'),
        ('정확히 7일 전', timedelta(days=REFUND_WINDOW_DAYS), 'ALLOW'),
        ('7일에서 1시간 모자람', timedelta(days=REFUND_WINDOW_DAYS, hours=-1), 'ALLOW'),
    ],
)
def test_7일_경계는_시간까지_본다(world, contract, label, age, expected):
    """`.days` 로 자르면 7일 23시간짜리가 '7일'이 되어 빠져나간다.

    원문은 "7일이 **지난**" 이므로 정확히 7일까지는 허용하고, 초과분만 거부한다.
    """
    order = _order(0, 22_500, number=f'T-BOUND-{int(age.total_seconds())}')
    # 경계는 마이크로초로 갈린다 — '지금'을 고정해 놓고 정확히 재 본다.
    now = order.created_at + age

    with mock.patch('orders.models.timezone.now', return_value=now):
        verdict = Refund.decide(order, 22_500)

    assert verdict.kind == getattr(Verdict, expected), f'{label} → {expected} 이어야 한다.'


@pytest.mark.django_db
def test_DENY_판정이면_환불이_커밋되지_않는다(world, contract):
    order = _order(REFUND_WINDOW_DAYS + 1, 22_500)

    ai = User.objects.get(username='ai-staff')

    verdict, outcome = Refund.apply(order, 22_500, '늦은 요청', ai)

    assert verdict.kind == Verdict.DENY
    assert outcome.state == Outcome.NOTHING, '거부된 요청은 승인 큐를 더럽히지 않는다.'
    assert outcome.refund is None
    assert not Refund.objects.filter(order=order).exists()
    order.refresh_from_db()
    assert order.status != Order.Status.CANCELLED


@pytest.mark.django_db
def test_기한_안_소액은_ALLOW_자동_승인(world, contract):
    """3일 지난 3만원 — 임계 아래다. 사람을 부르지 않는다."""
    order = _order(3, 30_000)

    ai = User.objects.get(username='ai-staff')

    verdict, outcome = Refund.apply(order, 30_000, '사이즈 불일치', ai)
    refund = outcome.refund

    assert verdict.kind == Verdict.ALLOW
    assert outcome.state == Outcome.COMMITTED, '판정이 ALLOW 인 것과 세계가 움직인 것은 다르다.'
    assert refund.status == Refund.Status.APPROVED
    assert refund.decided_by is None, '규칙이 확정한 것이지 사람이 확정한 것이 아니다.'
    assert refund.decided_via == 'rule'
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED


@pytest.mark.django_db
def test_기한_안_고액은_ESCALATE(world, contract):
    """3일 지난 7만원 — REFUND-002@v1. 점주에게 올라가고, 주문은 아직 안 움직인다."""
    order = _order(3, 70_000)

    ai = User.objects.get(username='ai-staff')

    verdict, outcome = Refund.apply(order, 70_000, '사이즈 불일치', ai)
    refund = outcome.refund

    assert verdict.kind == Verdict.ESCALATE
    assert REFUND_002 in verdict.rule_ids
    assert outcome.state == Outcome.QUEUED
    assert refund.status == Refund.Status.PROPOSED
    assert refund.decided_by is None
    order.refresh_from_db()
    assert order.status != Order.Status.CANCELLED
    _alternatives_are_real(verdict)


def _alternatives_are_real(verdict):
    """대안은 **실제로 갈 수 있는 길**만이어야 한다. 빈 목록도 정직한 답이다.

    없는 경로(예외 승인 큐·교환·재발송)를 약속하거나 승인 회피(분할 환불)를
    권하면 그건 대안이 아니다.
    """
    for alternative in verdict.alternatives:
        for banned in FORBIDDEN_ALTERNATIVES:
            assert banned not in alternative, (
                f'실행 경로가 없거나 승인을 회피하는 대안이다: {alternative!r}'
            )


@pytest.mark.django_db
def test_승인_저장이_실패하면_환불도_남지_않는다(world, contract):
    """환불 생성 → 승인 → 주문 취소는 하나의 트랜잭션이다.

    마지막 저장이 깨졌는데 환불만 `approved` 로 남으면, 장부는 "환불했다"는데
    주문은 살아 있게 된다. 그런 반쪽 상태가 생기지 않는지 실패를 주입해 본다.
    """
    order = _order(3, 30_000)
    ai = User.objects.get(username='ai-staff')

    with mock.patch.object(
        Order, 'save', side_effect=DatabaseError('주입한 저장 실패')
    ), pytest.raises(DatabaseError):
        Refund.apply(order, 30_000, '사이즈 불일치', ai)

    assert not Refund.objects.filter(order=order).exists(), '승인된 환불이 남아 있으면 안 된다.'
    order.refresh_from_db()
    assert order.status == Order.Status.PAID, '주문도 원래대로 돌아와야 한다.'


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
        order.mark_paid(order.user)

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

    order.mark_paid(order.user)

    order.refresh_from_db()
    assert order.status == Order.Status.PAID
    assert Product.objects.get(pk=product.pk).stock == before - 1


@pytest.mark.django_db
def test_두_번_호출해도_재고는_한_번만_깎인다(world, contract):
    """전이 계약 하나만으로 '두 번 깎이는 일'이 사라지는지 본다.

    **이것은 경합의 증명이 아니다.** 두 호출을 순서대로 낼 뿐이다. 그래도
    보이는 것이 있다 — 두 번째 호출은 `InvalidTransition` 으로 끊기고,
    재고는 한 번만 깎인다. 이사 전에는 두 번 다 통과해 두 번 깎였다(카드 ⑥).

    스레드를 쓰지 않는 이유도 학습거리다. Django 의 SQLite 테스트 DB 는
    shared-cache 인메모리라 두 스레드가 같은 테이블을 만지면 `OperationalError`
    (잠금 실패)가 먼저 난다. 그걸 '거부됨'으로 세면 계약이 지켜졌다는 **거짓
    통과**가 된다 — 잠금 실패는 도메인 DENY 가 아니다. 동기화된 경합 증명·잠금
    재시도·패자의 HTTP 응답은 **5단계**에서 제대로 다룬다.
    """
    InvalidTransition = contract
    order = _order(0, 18_000, status=Order.Status.PENDING, product_name='만년필 잉크 30ml')
    before = Product.objects.get(name='만년필 잉크 30ml').stock
    results = []

    for _ in range(2):
        try:
            Order.objects.get(pk=order.pk).mark_paid(order.user)
            results.append('ok')
        except InvalidTransition:
            results.append('denied')

    assert len(results) == 2, f'두 호출의 결과가 모두 수집돼야 한다: {results}'
    assert results.count('ok') == 1, f'정확히 한 번만 성공해야 한다: {results}'
    assert results.count('denied') == 1, f'나머지 한 번은 전이 계약이 거부해야 한다: {results}'
    assert Product.objects.get(name='만년필 잉크 30ml').stock == before - 1


@pytest.mark.django_db
def test_뒤_상품에서_재고가_모자라면_앞_상품_차감도_돌아온다(world, contract):
    """전이와 모든 재고 차감이 한 트랜잭션 안에 있는가.

    두 상품짜리 주문에서 두 번째 상품의 재고가 모자라면, 이미 깎아 놓은
    첫 번째 상품의 재고도 되돌아와야 한다. 상태 전이도 마찬가지다.
    """
    from orders.models import InsufficientStock

    first, second = Product.objects.all().order_by('pk')[:2]
    order = _order(0, 1_000, status=Order.Status.PENDING, number='T-ROLLBACK')
    for product, quantity in ((first, 1), (second, second.stock + 1)):
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
        )
    before_first, before_second = first.stock, second.stock

    with pytest.raises(InsufficientStock):
        order.mark_paid(order.user)

    assert Product.objects.get(pk=first.pk).stock == before_first, '앞 상품 차감이 복원돼야 한다.'
    assert Product.objects.get(pk=second.pk).stock == before_second
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING, '상태 전이도 함께 되돌아간다.'


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


def _ledger():
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / 'RULES.md').read_text(encoding='utf-8')


def test_규칙_대장에_세_행이_있다():
    """이사한 규칙은 대장에 남는다. 코드만 고치고 대장을 안 쓰면 반쪽이다."""
    ledger = _ledger()

    for rule_id in (REFUND_001, REFUND_002, ORDER_001):
        assert rule_id in ledger, f'{rule_id} 이(가) RULES.md 에 없다.'


def test_대장의_ID는_유일하다():
    """같은 ID 로 두 행을 쓰면 "어느 것이 시행 중인가"에 답할 수 없다.

    버전이 바뀌면 `@v2` 라는 **다른 ID** 로 새 행을 덧붙인다.
    """
    ledger = _ledger()

    for rule_id in (REFUND_001, REFUND_002, ORDER_001):
        rows = [line for line in ledger.splitlines() if line.startswith(f'| `{rule_id}`')]
        assert len(rows) == 1, f'{rule_id} 행이 {len(rows)}개다 — 대장의 ID 는 유일해야 한다.'


def test_대장의_원문은_보존된다():
    """대장은 **삭제 금지·내용 보존** 대장이다.

    원문은 사람이 말한 문장 그대로여야 한다. 코드가 바뀌었다고 원문을 고쳐
    맞추면 "그때 뭐라고 했었나"를 영영 알 수 없다. 원문을 바꿔야 할 상황이면
    그건 새 버전(`@v2`)이지 덮어쓰기가 아니다.
    """
    ledger = _ledger()

    for rule_id, text in RULE_TEXTS.items():
        assert text in ledger, f'{rule_id} 의 원문이 대장에서 사라지거나 바뀌었다: {text}'
