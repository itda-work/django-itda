"""PostgreSQL 실측 — 채점표가 아니다(2026-09-19, PG 프로브).

단계 채점표(`stage_*.py`)는 SQLite 위에서 법이 켜지는가를 잰다. 이 파일은 같은 세계를
PostgreSQL 에 올렸을 때 **SQLite 에서는 잴 수 없던 두 가지**를 잰다. `just test-pg` 로만
돈다 — 다른 백엔드에서는 모듈 전체를 건너뛴다.

1. `select_for_update(nowait=True)` 는 PG 에서 **실제 행 잠금**이다. 5단계
   `test_select_for_update는_SQLite에서_아무것도_잠그지_않는다` 의 짝이다 — 거기서는
   오류도 경고도 없이 무시되던 한 줄이, 여기서는 두 번째 연결을 `55P03` 으로 돌려보낸다.
   그리고 그 실패를 패키지(`django_itda.busy.is_lock_failure`)가 잠금으로 알아본다.
2. 7단계 sol 리뷰 발견 1 의 수정(조건부 UPDATE **뒤** 같은 트랜잭션에서 재고를 재조회)이
   READ COMMITTED 에서도 정확하다. 같은 상품을 담은 **서로 다른 주문 둘**을 동시에
   결제하면, `stock.deducted` 두 줄의 before/after 가 겹치지 않고 이어지며 끝값이 실제
   재고와 같다.

경합 규율은 5단계 `_race` 와 같다(`tests/stage_05_contention.py` 머리 "네 가지 준비").
Barrier 는 트랜잭션이 열리기 **직전**(`Order.mark_paid` 호출 앞)에 두고,
`django_db(transaction=True)` 로 다른 스레드가 데이터를 보게 하고, 스레드 끝에서
`connection.close()` 한다. 예외는 삼키지 않고 종류를 결과로 남긴다.
"""

import threading
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import OperationalError, connection, transaction

from django_itda.busy import is_lock_failure
from ledger.models import Event
from orders import services
from orders.models import Order
from shop.models import Product

pytestmark = pytest.mark.skipif(
    connection.vendor != 'postgresql',
    reason='PostgreSQL 실측 — just test-pg 로만 돈다.',
)

User = get_user_model()

SHIPPING = {
    'recipient_name': '박밥',
    'phone': '010-0000-0002',
    'address': '부산시 가상구 없는동 2-2',
}


def stamps():
    """재고 20개짜리 상품 — 두 주문이 같이 담을 자리."""
    return Product.objects.get(name='스탬프 세트 12종')


# --- 1. 행 잠금은 실제로 걸린다 -----------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_select_for_update_nowait는_PG에서_실제로_행을_잠근다(world):
    """한 연결이 행을 쥔 동안 다른 연결의 `NOWAIT` 은 기다리지 않고 `55P03` 이다.

    잡는 쪽은 스레드(자기 연결)다. 쥐고 있는 동안 본 스레드가 같은 행을
    `select_for_update(nowait=True)` 로 읽는다. SQLite 였다면 `FOR UPDATE` 가
    붙지 않아 평범한 SELECT 로 통과했을 자리다.
    """
    assert connection.features.has_select_for_update is True
    assert connection.features.has_select_for_update_nowait is True
    product = stamps()
    held = threading.Event()
    release = threading.Event()

    def holder():
        try:
            with transaction.atomic():
                list(Product.objects.select_for_update().filter(pk=product.pk))
                held.set()
                release.wait(timeout=10)
        finally:
            connection.close()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(timeout=10), '잡는 쪽 스레드가 행을 쥐지 못했다.'
        with pytest.raises(OperationalError) as raised, transaction.atomic():
            list(Product.objects.select_for_update(nowait=True).filter(pk=product.pk))
    finally:
        release.set()
        thread.join(timeout=10)

    assert raised.value.__cause__.sqlstate == '55P03', '55P03 = lock_not_available'
    assert is_lock_failure(raised.value), '패키지가 이것을 잠금(ToolBusy)으로 알아봐야 한다.'

    # 잠금이 풀리면 같은 질의가 그대로 통과한다 — 막은 것은 법이 아니라 잠금이었다.
    with transaction.atomic():
        assert list(Product.objects.select_for_update(nowait=True).filter(pk=product.pk))


# --- 2. READ COMMITTED 에서 재고 장부는 이어진다 -------------------------------------


def _race_mark_paid(workers):
    """스레드마다 `Order.mark_paid` 호출 **직전**에서 만났다가 동시에 출발한다.

    5단계 `_race` 와 같은 규율이다. 훅은 앱 코드에 심지 않고 `mock.patch` 로
    감싼다 — `mark_paid` 는 호출된 **뒤에** `atomic` 을 여므로 Barrier 는 잠금 밖이다.
    """
    barrier = threading.Barrier(len(workers), timeout=5)
    original = Order.mark_paid
    lock = threading.Lock()
    results = []

    def gated(self, *args, **kwargs):
        barrier.wait()
        return original(self, *args, **kwargs)

    def run(worker):
        try:
            value = worker()
        except Exception as exc:  # noqa: BLE001 — 종류를 결과로 남기는 것이 목적이다
            value = f'예외:{type(exc).__name__}'
        finally:
            connection.close()
        with lock:
            results.append(value)

    with mock.patch.object(Order, 'mark_paid', gated):
        threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

    assert len(results) == len(workers), f'모든 스레드의 결과가 모여야 한다: {results}'
    return results


@pytest.mark.django_db(transaction=True)
def test_서로_다른_주문_둘이_같은_상품을_동시에_결제해도_재고_장부는_이어진다(world):
    """READ COMMITTED 에서 뒤쪽 트랜잭션은 앞쪽의 UPDATE 가 커밋될 때까지 그 행에서 기다린다.

    풀려난 뒤의 조건부 UPDATE 는 **커밋된 새 값**에 다시 조건을 걸고, 같은 트랜잭션의
    재조회도 새 스냅샷(문장 단위)을 본다. 그래서 두 줄이 이어진다 — 20→18, 18→15.
    읽어 둔 스냅샷으로 적었다면 두 줄 다 20 에서 출발했을 것이다.
    """
    bob = User.objects.get(username='bob')
    product = stamps()
    start = product.stock
    assert start == 20
    quantities = (2, 3)
    orders = [services.intake_order(bob, [(product, q)], SHIPPING) for q in quantities]
    seen = set(
        Event.objects.filter(transition=Event.Transition.STOCK_DEDUCTED).values_list(
            'pk', flat=True
        )
    )

    def pay(order_pk):
        def worker():
            Order.objects.get(pk=order_pk).mark_paid(User.objects.get(username='bob'))
            return '결제'

        return worker

    results = _race_mark_paid([pay(order.pk) for order in orders])

    assert results == ['결제', '결제'], f'서로 다른 주문이라 둘 다 결제돼야 한다: {results}'
    for order in orders:
        order.refresh_from_db()
        assert order.status == Order.Status.PAID

    final = Product.objects.get(pk=product.pk).stock
    assert final == start - sum(quantities)

    rows = [
        row
        for row in Event.objects.filter(transition=Event.Transition.STOCK_DEDUCTED)
        if row.pk not in seen and row.subject_id == product.pk
    ]
    assert len(rows) == 2
    rows.sort(key=lambda row: row.before['stock'], reverse=True)
    first, second = rows
    assert first.before == {'stock': start}, '첫 줄은 경합 전 재고에서 출발한다.'
    assert second.before == first.after, '두 줄은 겹치지 않고 이어져야 한다.'
    assert second.after == {'stock': final}, '마지막 줄의 after 가 실제 재고다.'
    # 각 줄의 폭은 그 주문의 수량이다 — 어느 주문이 먼저였든.
    assert sorted(row.before['stock'] - row.after['stock'] for row in rows) == sorted(quantities)
