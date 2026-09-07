"""403 화면이 두 자리로 갈리는지 확인한다.

채점표가 아니다 — 단계 테스트가 아니라 사후 변경(2026-09-07)을 붙잡아 두는 회귀
테스트다. 콘솔은 "세계가 거부했다" 로 답하고, admin 은 교육 문구 없는 일반 403 으로
답한다.
"""

import pytest
from django.contrib.auth import get_user_model

from orders.models import Order, Refund

User = get_user_model()


@pytest.mark.django_db
def test_콘솔의_403은_세계의_목소리로_답한다(world, client):
    order = Order.objects.get(order_number='SEED-0002')
    refund = Refund.objects.create(
        order=order,
        amount=order.total_amount,
        reason='고객 요청: 사이즈 불일치',
        requested_by=User.objects.get(username='ai-staff'),
    )
    assert client.login(username='ai-staff', password='ai1234')

    response = client.post(f'/orders/refunds/{refund.pk}/approve/')

    assert response.status_code == 403
    body = response.content.decode()
    assert '세계가 거부했다' in body


@pytest.mark.django_db
def test_admin의_403은_교육_문구_없는_일반_403이다(world, client):
    assert client.login(username='owner', password='owner1234')

    response = client.get('/admin/auth/group/')

    assert response.status_code == 403
    body = response.content.decode()
    assert '세계가 거부했다' not in body
    assert '권한이 없습니다' in body
