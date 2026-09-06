"""실접속(live) 트랙 — 브라우저 밖에서 들어온 요청도 같은 법을 받는가.

이 파일은 **채점표가 아니다.** 단계 테스트(`stage_NN_*.py`)가 채점표고,
여기는 실접속 트랙이 fixture 와 같은 판정을 내는지 확인하는 회귀 테스트다.
MCP 서버는 이 API 를 감싼 얇은 껍데기이므로, 여기까지가 Django 의 몫이다.

확인하는 것은 넷이다.

1. 토큰 없으면 401, 틀린 토큰도 401 — 자리를 못 얻는다.
2. 자리를 얻어도 권한이 없으면 403 (`approve`).
3. 자격이 있어도 세계의 상태가 아니면 409 (`REFUND-001@v1`·`ORDER-001@v1`).
4. 5만원 초과는 202 — 승인 핸들만 받고 주문은 안 움직인다.
"""

import json

import pytest
from django.contrib.auth import get_user_model

from accounts.models import APIToken
from orders.models import Order, Refund
from orders.verdict import Outcome, Verdict

User = get_user_model()

ORDERS_URL = '/api/orders/'
REFUNDS_URL = '/api/refunds/'


def auth(token):
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


def post(client, url, token, **payload):
    return client.post(
        url, data=json.dumps(payload), content_type='application/json', **auth(token)
    )


@pytest.fixture
def ai_token(world):
    _, raw = APIToken.issue(User.objects.get(username='ai-staff'), 'test')
    return raw


@pytest.fixture
def owner_token(world):
    _, raw = APIToken.issue(User.objects.get(username='owner'), 'test')
    return raw


# --- 1. 인증 -----------------------------------------------------------------


@pytest.mark.django_db
def test_토큰이_없으면_401(client, world):
    response = client.get(ORDERS_URL)

    assert response.status_code == 401
    assert response.json()['error'] == 'unauthorized'


@pytest.mark.django_db
def test_틀린_토큰도_401(client, world):
    response = client.get(ORDERS_URL, **auth('없는-토큰'))

    assert response.status_code == 401


@pytest.mark.django_db
def test_토큰은_해시로만_저장되고_사용하면_흔적이_남는다(client, world):
    user = User.objects.get(username='ai-staff')
    token, raw = APIToken.issue(user, 'claude-code')

    assert token.key != raw
    assert token.last_used_at is None

    client.get(ORDERS_URL, **auth(raw))
    token.refresh_from_db()

    assert token.last_used_at is not None


# --- 2. 흐름: 접수 → 제안 → 403 → 점주 승인 ---------------------------------


@pytest.mark.django_db
def test_AI_직원_토큰으로_주문을_접수하면_200(client, ai_token):
    before = Order.objects.count()

    response = client.post(ORDERS_URL, data='{}', content_type='application/json', **auth(ai_token))
    body = response.json()

    assert response.status_code == 200
    assert body['kind'] == Verdict.ALLOW
    assert body['order']['status'] == Order.Status.PENDING
    assert Order.objects.count() == before + 1


@pytest.mark.django_db
def test_주문_목록과_상세를_읽는다(client, ai_token):
    listed = client.get(ORDERS_URL, **auth(ai_token)).json()['orders']
    numbers = {order['order_number'] for order in listed}

    assert {'SEED-0001', 'SEED-0002'} <= numbers

    order = Order.objects.get(order_number='SEED-0002')
    detail = client.get(f'{ORDERS_URL}{order.pk}/', **auth(ai_token)).json()

    assert detail['total_amount'] == 54_000
    assert detail['items'][0]['product_name'] == '만년필 잉크 30ml'


@pytest.mark.django_db
def test_5만원_초과_환불은_202_이고_주문은_안_움직인다(client, ai_token):
    order = Order.objects.get(order_number='SEED-0002')

    response = post(client, REFUNDS_URL, ai_token, order_id=order.pk, reason='사이즈 불일치')
    body = response.json()

    assert response.status_code == 202
    assert body['kind'] == Verdict.ESCALATE
    assert body['rule_ids'] == ['REFUND-002@v1']
    assert body['outcome'] == Outcome.QUEUED
    assert body['refund']['status'] == Refund.Status.PROPOSED
    assert body['refund']['check_url'] == f'/api/refunds/{body["refund"]["id"]}/'

    order.refresh_from_db()
    assert order.status == Order.Status.PAID  # 제안만으로는 세계가 안 움직인다


@pytest.mark.django_db
def test_AI_직원이_자기_제안을_승인하려_하면_403(client, ai_token):
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = post(client, REFUNDS_URL, ai_token, order_id=order.pk).json()['refund']['id']

    response = client.post(f'{REFUNDS_URL}{refund_id}/approve/', **auth(ai_token))
    body = response.json()

    assert response.status_code == 403
    assert body['error'] == 'forbidden'
    assert body['reason'] == 'AI 직원은 제안할 수 있지만 확정할 수 없다'
    assert Refund.objects.get(pk=refund_id).status == Refund.Status.PROPOSED


@pytest.mark.django_db
def test_점주_토큰으로는_승인되고_주문이_취소로_간다(client, ai_token, owner_token):
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = post(client, REFUNDS_URL, ai_token, order_id=order.pk).json()['refund']['id']

    response = client.post(f'{REFUNDS_URL}{refund_id}/approve/', **auth(owner_token))

    assert response.status_code == 200
    assert response.json()['refund']['status'] == Refund.Status.APPROVED

    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED

    refund = Refund.objects.get(pk=refund_id)
    assert refund.decided_by.username == 'owner'
    assert refund.decided_via == Refund.Via.OWNER


@pytest.mark.django_db
def test_제안한_쪽은_승인_상태를_물어볼_수_있다(client, ai_token, owner_token):
    order = Order.objects.get(order_number='SEED-0002')
    refund_id = post(client, REFUNDS_URL, ai_token, order_id=order.pk).json()['refund']['id']

    waiting = client.get(f'{REFUNDS_URL}{refund_id}/', **auth(ai_token))

    assert waiting.status_code == 202
    assert waiting.json()['outcome'] == Outcome.ALREADY

    client.post(f'{REFUNDS_URL}{refund_id}/approve/', **auth(owner_token))
    settled = client.get(f'{REFUNDS_URL}{refund_id}/', **auth(ai_token))

    assert settled.status_code == 200
    assert settled.json()['kind'] == Verdict.ALLOW


# --- 3. 세계의 상태가 거부하는 자리 — 409 ------------------------------------


@pytest.mark.django_db
def test_8일_지난_주문_환불은_409_REFUND_001(client, ai_token):
    order = Order.objects.get(order_number='SEED-0001')

    response = post(client, REFUNDS_URL, ai_token, order_id=order.pk, reason='늦게 확인함')
    body = response.json()

    assert response.status_code == 409
    assert body['kind'] == Verdict.DENY
    assert body['rule_ids'] == ['REFUND-001@v1']
    assert body['outcome'] == Outcome.NOTHING
    assert body['alternatives']  # 거절만 하고 길을 안 알려주면 우회를 시도한다
    assert not Refund.objects.filter(order=order).exists()


@pytest.mark.django_db
def test_이미_결제된_주문의_재결제는_409_ORDER_001(client, ai_token):
    order = Order.objects.get(order_number='SEED-0002')

    response = client.post(f'{ORDERS_URL}{order.pk}/pay/', **auth(ai_token))
    body = response.json()

    assert response.status_code == 409
    assert body['rule_ids'] == ['ORDER-001@v1']

    order.refresh_from_db()
    assert order.status == Order.Status.PAID


@pytest.mark.django_db
def test_결제_대기_주문은_결제되고_이_경로도_같은_계약을_지난다(client, ai_token):
    order = Order.objects.get(order_number='SEED-0004')

    first = client.post(f'{ORDERS_URL}{order.pk}/pay/', **auth(ai_token))
    second = client.post(f'{ORDERS_URL}{order.pk}/pay/', **auth(ai_token))

    assert first.status_code == 200
    assert second.status_code == 409  # 두 번째는 pending 인 행이 없다


@pytest.mark.django_db
def test_고객_토큰은_주문을_접수할_수_없다(client, world):
    _, raw = APIToken.issue(User.objects.get(username='alice'), 'test')

    response = client.post(ORDERS_URL, data='{}', content_type='application/json', **auth(raw))

    assert response.status_code == 403
