"""1단계 — 격상(ESCALATE): 점주가 누르기 전엔 세계가 안 움직인다.

검사하는 것은 딱 하나, **누가 확정하는가**다.
AI 직원은 제안까지만 할 수 있고, 주문 상태를 바꾸는 것은 점주의 승인뿐이다.
"""

import pytest
from django.contrib.auth import get_user_model

from orders.models import Order, Refund

User = get_user_model()

PROPOSE_URL = '/agent/act/refund-size/'
INTAKE_URL = '/agent/act/order-intake/'


def _approve_url(refund):
    return f'/orders/refunds/{refund.pk}/approve/'


def _reject_url(refund):
    return f'/orders/refunds/{refund.pk}/reject/'


def _proposed_refund(order_number='SEED-0002'):
    """AI 직원이 올린 환불 제안 한 건."""
    order = Order.objects.get(order_number=order_number)
    return Refund.objects.create(
        order=order,
        amount=order.total_amount,
        reason='고객 요청: 사이즈 불일치',
        requested_by=User.objects.get(username='ai-staff'),
    )


@pytest.mark.django_db
def test_환불은_제안_상태로_태어난다(world):
    refund = _proposed_refund()

    assert refund.status == Refund.Status.PROPOSED
    assert refund.decided_by is None
    assert refund.decided_at is None


@pytest.mark.django_db
def test_AI_직원의_주문_접수는_바로_커밋된다(world, client):
    assert client.login(username='ai-staff', password='ai1234')
    before = Order.objects.count()

    response = client.post(INTAKE_URL)

    assert response.status_code in (200, 302)
    assert Order.objects.count() == before + 1
    assert Order.objects.order_by('-pk').first().status == Order.Status.PENDING


@pytest.mark.django_db
def test_AI_직원의_환불_제안은_주문을_움직이지_못한다(world, client):
    assert client.login(username='ai-staff', password='ai1234')

    response = client.post(PROPOSE_URL)

    assert response.status_code in (200, 302)
    refund = Refund.objects.get(order__order_number='SEED-0002')
    assert refund.status == Refund.Status.PROPOSED
    assert refund.requested_by.username == 'ai-staff'
    # 세계는 아직 움직이지 않았다.
    assert Order.objects.get(order_number='SEED-0002').status == Order.Status.PAID


@pytest.mark.django_db
def test_AI_직원이_직접_승인하면_403(world, client):
    refund = _proposed_refund()
    assert client.login(username='ai-staff', password='ai1234')

    response = client.post(_approve_url(refund))

    assert response.status_code == 403
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROPOSED
    assert refund.decided_by is None
    assert Order.objects.get(order_number='SEED-0002').status == Order.Status.PAID


@pytest.mark.django_db
def test_점주가_승인하면_주문이_취소된다(world, client):
    refund = _proposed_refund()
    assert client.login(username='owner', password='owner1234')

    response = client.post(_approve_url(refund))

    assert response.status_code in (200, 302)
    refund.refresh_from_db()
    assert refund.status == Refund.Status.APPROVED
    assert refund.decided_by.username == 'owner'
    assert refund.decided_at is not None
    assert Order.objects.get(order_number='SEED-0002').status == Order.Status.CANCELLED


@pytest.mark.django_db
def test_점주가_거부하면_주문은_그대로다(world, client):
    refund = _proposed_refund('SEED-0003')
    assert client.login(username='owner', password='owner1234')

    response = client.post(_reject_url(refund))

    assert response.status_code in (200, 302)
    refund.refresh_from_db()
    assert refund.status == Refund.Status.REJECTED
    assert refund.decided_by.username == 'owner'
    assert refund.decided_at is not None
    assert Order.objects.get(order_number='SEED-0003').status == Order.Status.PAID


@pytest.mark.django_db
def test_admin_승인_액션도_같은_결과다(world, client):
    refund = _proposed_refund()
    assert client.login(username='owner', password='owner1234')

    response = client.post(
        '/admin/orders/refund/',
        {'action': 'approve_selected', '_selected_action': [str(refund.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    refund.refresh_from_db()
    assert refund.status == Refund.Status.APPROVED
    assert refund.decided_by.username == 'owner'
    assert Order.objects.get(order_number='SEED-0002').status == Order.Status.CANCELLED


@pytest.mark.django_db
def test_점주는_superuser가_아니다(world):
    """승인 권한은 '전능함'이 아니라 그룹 권한에서 나온다."""
    owner = User.objects.get(username='owner')

    assert owner.is_staff
    assert not owner.is_superuser
    assert owner.has_perm('orders.change_refund')


@pytest.mark.django_db
def test_AI_직원은_제안_권한만_가진다(world):
    ai_staff = User.objects.get(username='ai-staff')

    assert not ai_staff.is_staff
    assert ai_staff.has_perm('orders.add_refund')
    assert ai_staff.has_perm('orders.add_order')
    assert not ai_staff.has_perm('orders.change_refund')
