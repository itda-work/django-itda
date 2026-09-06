"""0단계 — 세계가 서 있는가.

법을 검사하지 않는다. 세계가 재현 가능하게 서고, 점주만 admin에 들어간다는
두 가지만 확인한다.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from orders.models import Order, OrderItem, Refund
from shop.models import Category, Product

User = get_user_model()


def _counts():
    return {
        'user': User.objects.count(),
        'category': Category.objects.count(),
        'product': Product.objects.count(),
        'order': Order.objects.count(),
        'order_item': OrderItem.objects.count(),
    }


@pytest.mark.django_db
def test_seed_world_는_멱등이다():
    call_command('seed_world', verbosity=0)
    first = _counts()

    call_command('seed_world', verbosity=0)
    second = _counts()

    assert first == second
    assert first == {'user': 5, 'category': 2, 'product': 6, 'order': 4, 'order_item': 6}


@pytest.mark.django_db
def test_환불은_아직_한_건도_없다(world):
    assert Refund.objects.count() == 0


@pytest.mark.django_db
def test_점주는_admin_주문_목록을_본다(world, client):
    assert client.login(username='owner', password='owner1234')

    response = client.get('/admin/orders/order/')

    assert response.status_code == 200


@pytest.mark.django_db
def test_AI_직원은_admin에_들어가지_못한다(world, client):
    assert client.login(username='ai-staff', password='ai1234')

    response = client.get('/admin/orders/order/')

    assert response.status_code == 302
