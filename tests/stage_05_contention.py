"""5단계 — 경합: 같은 사건이 두 번 일어나는가.

4단계의 rowcount 계약은 **UPDATE 경합**을 버틴다. 조건을 걸 행이 이미 있기
때문이다. 환불 제안은 **INSERT** 라 조건을 걸 행이 없다 — 그래서 이번 법은
코드가 아니라 **DB 제약**으로 내려간다.

시작 상태(`stage-05-start`)에서는 11개 중 8개가 실패하고 3개가 통과한다.
통과하는 셋 중 하나(`test_동시_결제_…`)가 이 단계의 소재다. 결제는 버티는데
환불은 못 버틴다. 왜 다른가.

## 스레드가 여기서는 되는 이유 — 네 가지 준비

4단계 테스트에는 "SQLite 테스트 DB 는 shared-cache 인메모리라 두 스레드가 같은
테이블을 만지면 `OperationalError` 가 먼저 난다"고 적혀 있었다. 그 잠금 실패를
'거부됨'으로 세면 계약이 지켜졌다는 **거짓 통과**가 된다. 그래서 이번 단계는
경합이 도메인 판정으로 갈리도록 네 가지를 미리 깔아 둔다.

1. **테스트 DB 가 파일이다** — `config/settings.py` 의 `DATABASES['default']['TEST']`.
   인메모리 shared-cache 가 아니어야 두 연결이 정상적으로 줄을 선다(테스트 10).
2. **`django_db(transaction=True)`** — 기본 마커는 테스트를 트랜잭션으로 감싸므로
   다른 스레드의 연결이 이 데이터를 아예 못 본다. 경합 테스트에는 못 쓴다.
3. **Barrier 는 잠금 밖에 둔다** — 두 스레드가 만나는 지점에 열린 트랜잭션이
   있으면 서로를 기다리다 timeout 까지 멈춘다. 그래서 훅은 앱 코드에 심지 않고
   `mock.patch` 로 감싸되, 지점은 **트랜잭션이 열리기 직전**으로 고른다.
4. **스레드 끝에서 `connection.close()`** — 안 닫으면 파일 핸들이 남아 teardown 이
   흔들린다. 예외는 삼키지 않고 종류를 결과에 남긴다. `OperationalError` 가
   섞이면 그 자체로 실패다 — **잠금 실패는 도메인 DENY 가 아니다.**
"""

import json
import threading
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import Client

from accounts.models import APIToken
from orders import services
from orders.models import InvalidTransition, Order, Refund
from orders.rules import ORDER_001, REFUND_001, REFUND_002, RULE_TEXTS
from orders.verdict import Outcome, Verdict
from shop.models import Product

User = get_user_model()

ORDERS_URL = '/api/orders/'
REFUNDS_URL = '/api/refunds/'

# 시작 상태에는 아직 없는 이름들이다. 여기서 죽으면 파일 전체가 수집조차 안 돼
# '무엇이 몇 개 실패했는지'가 안 보이므로, 기대값을 문자열로 들고 있는다.
try:
    from orders.rules import REFUND_003
except ImportError:  # 5단계 시작 상태
    REFUND_003 = 'REFUND-003@v1'
REFUND_003_TEXT = '같은 환불을 두 번 처리하지 마라. 한 주문에 환불은 한 번이다.'
REPLAYED = getattr(Outcome, 'REPLAYED', 'REPLAYED')


# --- 경합 도구 -----------------------------------------------------------------


def _gate(barrier, owner, name):
    """`owner.name` 호출 **직전**에 Barrier 를 끼우는 patch 를 만든다.

    앱 코드에 훅을 심지 않는 이유는 하나다 — 시험을 위해 세계를 바꾸면
    무엇을 실측한 것인지 알 수 없다. 지점은 어느 트랜잭션도 열려 있지 않은
    곳으로 고른다(파일 docstring 3번).
    """
    attribute = owner.__dict__.get(name)
    if isinstance(attribute, classmethod):
        function = attribute.__func__

        def gated_classmethod(cls, *args, **kwargs):
            barrier.wait()
            return function(cls, *args, **kwargs)

        return mock.patch.object(owner, name, classmethod(gated_classmethod))

    original = getattr(owner, name)

    def gated(self, *args, **kwargs):
        barrier.wait()
        return original(self, *args, **kwargs)

    return mock.patch.object(owner, name, gated)


def _race(worker, owner, hook, n=2):
    """스레드 `n` 개를 `owner.hook` 앞에서 만나게 했다가 동시에 풀어 준다.

    돌려주는 것은 `worker(i)` 의 반환값 목록이다. 예외는 삼키지 않고
    `'예외:<종류>'` 로 남긴다 — 단언은 "예상한 두 결과만"이어야 하고,
    잠금 실패(`OperationalError`)를 거부로 세면 거짓 통과가 된다.
    """
    barrier = threading.Barrier(n, timeout=5)
    lock = threading.Lock()
    results = []

    def run(index):
        try:
            value = worker(index)
        except Exception as exc:  # noqa: BLE001 — 종류를 결과로 남기는 것이 목적이다
            value = f'예외:{type(exc).__name__}'
        finally:
            connection.close()
        with lock:
            results.append(value)

    with _gate(barrier, owner, hook):
        threads = [threading.Thread(target=run, args=(index,)) for index in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

    assert len(results) == n, f'{n}개 스레드의 결과가 모두 수집돼야 한다: {results}'
    return results


def auth(token):
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


def post(client, url, token, headers=None, **payload):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type='application/json',
        **auth(token),
        **(headers or {}),
    )


@pytest.fixture
def ai(world):
    return User.objects.get(username='ai-staff')


@pytest.fixture
def owner(world):
    return User.objects.get(username='owner')


# --- 1. INSERT 경합 — 코드가 아니라 DB 가 막는다 --------------------------------


@pytest.mark.django_db
def test_살아_있는_환불은_주문당_하나다_DB제약(world, ai):
    """3단계의 `CHECK ("stock" >= 0)` 와 같은 층이다 — 경로를 안 지나도 걸린다.

    서비스 함수도, 판정도, 뷰도 지나지 않는다. shell 에서 `create()` 를 두 번
    부른다. DB 제약이면 여기서 `IntegrityError` 가 나고, 애플리케이션 검증이면
    아무 일 없이 두 건이 생긴다. 4단계 세 법과 **층이 다르다**는 것이 이 한 줄로 갈린다.
    """
    order = Order.objects.get(order_number='SEED-0003')
    Refund.objects.create(
        order=order, amount=30_000, reason='첫 번째', requested_by=ai, status=Refund.Status.APPROVED
    )

    with pytest.raises(IntegrityError):
        Refund.objects.create(
            order=order,
            amount=30_000,
            reason='두 번째',
            requested_by=ai,
            status=Refund.Status.APPROVED,
        )


@pytest.mark.django_db(transaction=True)
def test_동시_제안_두_개_중_하나만_확정된다(world, ai):
    """스레드 둘이 `Refund.decide` 앞에서 만났다가 동시에 출발한다.

    시작 상태에서는 둘 다 `COMMITTED` 다 — `services.propose_refund` 의
    사전 조회(`filter().first()`)를 **둘이 같이** 통과하기 때문이다.
    조회와 생성 사이가 벌어진 자리, check-then-act 다.
    """
    order = Order.objects.get(order_number='SEED-0003')

    def worker(_index):
        actor = User.objects.get(username='ai-staff')
        target = Order.objects.get(pk=order.pk)
        _, outcome = services.propose_refund(actor, target, 30_000, '사이즈 불일치')
        return outcome.state

    results = _race(worker, Refund, 'decide')

    assert sorted(results) == [Outcome.ALREADY, Outcome.COMMITTED], (
        f'하나는 확정, 하나는 "이미 있다"여야 한다: {results}'
    )
    assert Refund.objects.filter(order=order, status=Refund.Status.APPROVED).count() == 1
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED


@pytest.mark.django_db(transaction=True)
def test_동시_결제_두_개_중_하나만_통과한다_HTTP(world, ai):
    """**시작 상태에서도 통과한다.** 4단계 계약이 UPDATE 경합을 이미 버틴다.

    4단계에서는 두 호출을 순서대로 냈을 뿐이라 경합의 증명이 아니었다. 여기서는
    동기화된 두 스레드가 HTTP 로 같은 주문을 결제한다. 그래도 재고는 한 번만 깎인다 —
    `WHERE status = 'pending'` 을 걸 **행이 이미 있기** 때문이다.

    그럼 환불은 왜 못 버티는가. INSERT 에는 조건을 걸 행이 없다.
    """
    _, token = APIToken.issue(ai, 'race')
    order = Order.objects.get(order_number='SEED-0004')
    product = Product.objects.get(name='한정판 흑임자 다쿠아즈')
    assert product.stock == 1, '한정 재고 1개짜리 주문이어야 경합이 보인다.'

    def worker(_index):
        response = Client().post(f'{ORDERS_URL}{order.pk}/pay/', **auth(token))
        return response.status_code, response.json().get('rule_ids', [])

    results = _race(worker, Order, 'mark_paid')

    assert sorted(code for code, _ in results) == [200, 409], f'{results}'
    loser = [rules for code, rules in results if code == 409][0]
    assert ORDER_001 in loser, f'패자는 규칙 ID 를 들고 돌아와야 한다: {loser}'
    assert Product.objects.get(pk=product.pk).stock == 0, '재고는 한 번만 깎인다.'


# --- 2. 재전송 — 같은 열쇠는 같은 답 --------------------------------------------


@pytest.mark.django_db
def test_같은_열쇠로_재전송하면_그때의_답을_다시_받는다(world, ai):
    """`Idempotency-Key` 를 들고 같은 요청이 두 번 온다. 새 사건이 아니다.

    두 번째 응답은 **다시 판정한 결과**가 아니라 **그때 저장해 둔 판정**이다.
    그래서 `outcome` 이 `ALREADY`(다른 요청인데 이미 처리된 건이 있다)가 아니라
    `REPLAYED`(같은 요청이 다시 왔다)다.
    """
    _, token = APIToken.issue(ai, 'live')
    order = Order.objects.get(order_number='SEED-0002')
    client = Client()
    key = {'HTTP_IDEMPOTENCY_KEY': 'k1'}

    def send():
        """같은 요청을 글자 하나 다르지 않게 보낸다 — 열쇠까지 같다."""
        return post(
            client, REFUNDS_URL, token, headers=key, order_id=order.pk, reason='사이즈 불일치'
        )

    first, second = send(), send()

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json()['kind'] == Verdict.ESCALATE
    assert first.json()['refund']['id'] == second.json()['refund']['id']
    assert first.json()['outcome'] == Outcome.QUEUED
    assert second.json()['outcome'] == REPLAYED, '재전송은 재판정이 아니다.'
    assert second.json()['rule_ids'] == [REFUND_002], '그때의 판정이 그대로 돌아와야 한다.'
    assert Refund.objects.filter(order=order).count() == 1


@pytest.mark.django_db
def test_다른_열쇠의_두_번째_요청은_새_환불이_아니다(world, ai):
    """열쇠가 다르면 재전송이 아니다 — 그래도 살아 있는 환불은 주문당 하나다.

    `REPLAYED` 와 `ALREADY` 를 가르는 자리다. 앞은 "그때의 답",
    뒤는 "지금 상태". 어느 쪽이든 환불 행은 하나뿐이다.
    """
    order = Order.objects.get(order_number='SEED-0003')

    Refund.apply(order, 30_000, '첫 번째', ai, idempotency_key='a')
    _, outcome = Refund.apply(order, 30_000, '두 번째', ai, idempotency_key='b')

    assert outcome.state == Outcome.ALREADY
    assert Refund.objects.filter(order=order).count() == 1


# --- 3. UPDATE 경합 — 확정은 제안 상태에서만 -------------------------------------


@pytest.mark.django_db
def test_확정은_제안_상태에서만_승인_뒤_거부(world, ai, owner):
    """이미 승인된 환불을 거부로 덮어쓸 수 있는가.

    시작 상태의 `Refund.reject` 는 그냥 `save()` 다 — 지금 상태를 묻지 않는다.
    점주가 승인 버튼을 누른 뒤 거부 버튼을 눌러도 그대로 덮어쓴다. 그러면 주문은
    취소된 채 환불은 '거부'로 남는다. 장부가 거짓말을 한다.
    """
    order = Order.objects.get(order_number='SEED-0002')
    _, outcome = Refund.apply(order, 54_000, '사이즈 불일치', ai)
    refund = outcome.refund
    refund.approve(owner)

    with pytest.raises(InvalidTransition) as caught:
        refund.reject(owner, note='역시 안 되겠다')

    assert REFUND_003 in caught.value.verdict.rule_ids
    refund.refresh_from_db()
    assert refund.status == Refund.Status.APPROVED
    order.refresh_from_db()
    assert order.status == Order.Status.CANCELLED


@pytest.mark.django_db(transaction=True)
def test_점주_둘이_동시에_승인하면_하나만_확정된다_HTTP(world, ai, owner):
    """점주가 두 창에서 같은 제안을 동시에 승인한다.

    시작 상태의 `orders/api.py:refund_approve` 는 `status != PROPOSED` 를 **먼저
    조회해서** 검사한다. 조회와 확정 사이가 벌어진 자리라 둘 다 통과한다 —
    4단계에 그대로 남아 있던 check-then-act 다.
    """
    order = Order.objects.get(order_number='SEED-0002')
    _, outcome = Refund.apply(order, 54_000, '사이즈 불일치', ai)
    refund_pk = outcome.refund.pk
    tokens = [APIToken.issue(owner, f'창{index}')[1] for index in range(2)]

    def worker(index):
        response = Client().post(f'{REFUNDS_URL}{refund_pk}/approve/', **auth(tokens[index]))
        return response.status_code

    results = _race(worker, Refund, 'approve')

    assert sorted(results) == [200, 409], f'하나는 확정, 하나는 조건 불일치여야 한다: {results}'
    refund = Refund.objects.get(pk=refund_pk)
    assert refund.status == Refund.Status.APPROVED
    assert refund.decided_via == Refund.Via.OWNER


# --- 4. 잠금 실패는 판정이 아니다 -----------------------------------------------


@pytest.mark.django_db
def test_잠금_실패는_거부가_아니다_503(world, ai):
    """`database is locked` 를 409 로 돌려주면 세계가 거짓말을 하는 것이다.

    409 는 "자격은 있지만 세계가 지금 그 상태가 아니다"이고, 잠금 실패는
    "판정하지 못했다"이다. 다시 보내면 될 요청에 규칙 위반이라고 답하면
    AI 직원은 있지도 않은 규칙을 고객에게 설명한다.

    실제 잠금은 환경에 따라 재현되지 않으므로 **주입**해서 본다. 이 테스트가
    재는 것은 잠금의 빈도가 아니라 잠금 실패를 무엇으로 번역하는가다.
    """
    _, token = APIToken.issue(ai, 'live')
    order = Order.objects.get(order_number='SEED-0004')
    client = Client(raise_request_exception=False)

    with mock.patch(
        'orders.services.pay_order', side_effect=OperationalError('database is locked')
    ):
        response = client.post(f'{ORDERS_URL}{order.pk}/pay/', **auth(token))

    assert response.status_code == 503
    assert response['Retry-After'] == '1'
    body = response.json()
    assert body['error'] == 'busy'
    assert 'kind' not in body, '판정이 아니다 — ALLOW/DENY/ESCALATE 를 실어 보내면 안 된다.'
    order.refresh_from_db()
    assert order.status == Order.Status.PENDING


# --- 5. 실측: 잠갔다고 믿는 잠금 · 준비 확인 -------------------------------------


@pytest.mark.django_db
def test_select_for_update는_SQLite에서_아무것도_잠그지_않는다(world):
    """`select_for_update()` 는 오류도 경고도 없이 **무시된다**.

    Django 는 백엔드가 지원할 때만 `FOR UPDATE` 를 붙인다. SQLite 는
    `has_select_for_update = False` 라 그냥 평범한 SELECT 가 나간다.
    "잠갔다고 믿는데 안 잠긴 잠금"이 이 단계에서 실측하는 것 하나다.

    PostgreSQL 이면 행 잠금이 걸린다. 그건 8단계 전에는 확인할 수 없다
    (이 과정은 8단계까지 외부 서비스 0이다).
    """
    assert connection.features.has_select_for_update is False

    with transaction.atomic():
        orders = list(Order.objects.select_for_update().filter(status=Order.Status.PAID))

    assert orders, '잠금이 아니라 평범한 SELECT 로 실행됐고, 행도 그대로 나온다.'


@pytest.mark.django_db
def test_테스트_DB는_파일이다(world):
    """준비 확인 — 이게 아니면 아래 경합 테스트가 무엇을 쟀는지 알 수 없다."""
    name = str(connection.settings_dict['NAME'])

    assert 'memory' not in name, (
        f'테스트 DB 가 인메모리다({name}). 인메모리 shared-cache 에서는 스레드 경합이 '
        '도메인 판정에 닿기 전에 잠금 오류로 먼저 죽는다 — '
        "config/settings.py 의 DATABASES['default']['TEST'] 를 확인하라."
    )


# --- 6. 규칙 대장 ---------------------------------------------------------------


def _ledger():
    return (Path(__file__).resolve().parent.parent / 'RULES.md').read_text(encoding='utf-8')


def test_규칙_대장에_REFUND_003이_있고_앞_행은_그대로다():
    """대장은 append-only 다. 새 행이 늘어도 앞 행의 원문은 한 글자도 안 바뀐다."""
    ledger = _ledger()

    rows = [line for line in ledger.splitlines() if line.startswith(f'| `{REFUND_003}`')]
    assert len(rows) == 1, f'{REFUND_003} 행이 {len(rows)}개다 — 대장의 ID 는 유일해야 한다.'
    assert REFUND_003_TEXT in ledger, '점주가 말한 원문이 대장에 그대로 있어야 한다.'

    for rule_id in (REFUND_001, REFUND_002, ORDER_001):
        assert RULE_TEXTS[rule_id] in ledger, (
            f'{rule_id} 의 원문이 바뀌었다 — 대장은 삭제·수정 금지다.'
        )
