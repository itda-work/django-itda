"""5단계 — 경합: 같은 사건이 두 번 일어나는가.

4단계의 rowcount 계약은 **UPDATE 경합**을 버틴다. 조건을 걸 행이 이미 있기
때문이다. 환불 제안은 **INSERT** 라 조건을 걸 행이 없다 — 그래서 이번 법은
코드가 아니라 **DB 제약**으로 내려간다.

시작 상태(`stage-05-start`)에서 21개 중 15개가 실패한다. 통과하는 여섯은
성격이 셋으로 갈리므로 섞어 세지 않는다.

- **이미 켜진 법** — 동시 결제(4단계 rowcount 계약) · 대장 세 행 보존.
- **실측·준비 확인** — `select_for_update` 무효 · 테스트 DB 가 파일인지.
- **회귀 방지** — 스키마 오류가 500 인지 · 열쇠 없는 재신청이 지금 상태를 듣는지.
  뒤엣것은 시작 상태에서는 서비스 층의 사전 조회가 우연히 같은 답을 주기 때문에
  통과한다. 그 조회를 걷어낼 때 답이 나빠지지 않는지를 붙잡아 두는 자리다.

첫째가 이 단계의 소재다. 결제는 버티는데 환불은 못 버틴다. 왜 다른가.

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
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import Client
from django.utils import timezone

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

# `stage-04-done` 시점 대장 세 행의 ID·원문·출처. 고정값이다 — 여기를 고쳐야
# 테스트가 통과한다면, 그건 append-only 대장을 덮어썼다는 뜻이다.
STAGE_04_ROWS = {
    REFUND_001: (
        '결제 후 7일이 지난 주문은 환불하지 마라.',
        '`agent/prompts/ai_staff.md` 1번 (점주, 2026-09-06)',
    ),
    REFUND_002: (
        '5만원을 초과하는 환불은 반드시 점주 승인을 받아라.',
        '`agent/prompts/ai_staff.md` 2번 (점주, 2026-09-06)',
    ),
    ORDER_001: (
        '이미 결제 완료된 주문을 다시 결제 완료로 만들지 마라.',
        '`agent/prompts/ai_staff.md` 3번 (점주, 2026-09-06)',
    ),
}


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


@pytest.mark.django_db
def test_제안_상태의_중복도_DB가_막는다(world, ai):
    """조건절이 `approved` 만이면 승인 큐에 같은 주문이 두 번 쌓인다.

    막아야 하는 것은 "확정된 환불이 둘"이 아니라 **살아 있는 환불이 둘**이다.
    """
    order = Order.objects.get(order_number='SEED-0002')
    Refund.objects.create(order=order, amount=54_000, reason='첫 번째', requested_by=ai)

    with pytest.raises(IntegrityError):
        Refund.objects.create(order=order, amount=54_000, reason='두 번째', requested_by=ai)


@pytest.mark.django_db
def test_거부된_뒤에는_다시_신청할_수_있고_빈_열쇠는_제약_밖이다(world, ai):
    """**부분** 유일이라는 것이 여기서 갈린다.

    주문 전체에 무조건 유일 제약을 걸면 거부된 건이 있는 주문에는 영영 환불을
    못 올린다. 거부는 사건의 끝이고 그 뒤의 새 제안은 **다른 사건**이다.
    빈 열쇠도 마찬가지다 — 열쇠를 안 준 요청 둘을 같은 요청이라고 부를 근거가 없다.
    """
    order = Order.objects.get(order_number='SEED-0002')
    for note in ('첫 번째 거부', '두 번째 거부'):
        Refund.objects.create(
            order=order,
            amount=54_000,
            reason=note,
            requested_by=ai,
            status=Refund.Status.REJECTED,
            idempotency_key='',
        )

    verdict, outcome = Refund.apply(order, 54_000, '다시 신청', ai)

    assert outcome.state == Outcome.QUEUED, '거부된 건만 있으면 새 제안이 올라가야 한다.'
    assert verdict.kind == Verdict.ESCALATE
    assert Refund.objects.filter(order=order).count() == 3


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
def test_같은_열쇠에_다른_금액을_실어도_최초_내용이_이긴다(world, ai):
    """정책 고정 — **같은 열쇠는 최초 내용 우선**이고, 내용 충돌은 검사하지 않는다.

    열쇠는 요청의 **표찰**이다. 표찰이 같으면 같은 요청이고, 같은 요청에는
    같은 답이 간다. "금액이 달라졌으니 다른 요청 아닌가"는 다른 정책이고
    (충돌을 400 으로 되돌려주는 세계도 있다) 그쪽을 고르지 않았을 뿐이다.
    어느 쪽이든 **고르고 적어야** 하는 계약이라 테스트로 못 박아 둔다.
    """
    _, token = APIToken.issue(ai, 'live')
    order = Order.objects.get(order_number='SEED-0002')
    client = Client()
    key = {'HTTP_IDEMPOTENCY_KEY': 'k-충돌'}

    first = post(client, REFUNDS_URL, token, headers=key, order_id=order.pk)
    second = post(client, REFUNDS_URL, token, headers=key, order_id=order.pk, amount=30_000)

    assert second.status_code == 202, '30,000원으로 새로 판정했다면 200 ALLOW 였을 것이다.'
    assert second.json()['outcome'] == REPLAYED
    assert second.json()['refund']['amount'] == first.json()['refund']['amount'] == 54_000
    assert second.json()['rule_ids'] == [REFUND_002]
    assert Refund.objects.filter(order=order).count() == 1


@pytest.mark.django_db
def test_승인된_뒤_기한이_지나도_같은_열쇠는_그때의_답을_받는다(world, ai, owner):
    """**일어난 일은 일어난 일이다.** 재전송은 다시 허가를 구하는 것이 아니다.

    저장된 사건의 재생이 신규 판정보다 뒤에 있으면 이런 일이 난다 — 점주가
    승인까지 끝낸 건에 같은 열쇠로 재전송했더니, 그 사이 7일이 지났다는
    이유로 "기한 초과라 거부합니다"(409 DENY)가 돌아온다. 이미 환불된 건을
    신규 기한 초과 요청처럼 설명하는 것이라 명백한 거짓말이다.

    재생 범위도 여기서 고정한다 — 돌아오는 것은 **저장된 판정과 그 판정의
    HTTP 코드**이고, 함께 실리는 객체 상태는 **지금 값**이다.
    """
    _, ai_token = APIToken.issue(ai, 'live')
    _, owner_token = APIToken.issue(owner, 'live')
    order = Order.objects.get(order_number='SEED-0002')
    client = Client()
    key = {'HTTP_IDEMPOTENCY_KEY': 'k9'}

    first = post(client, REFUNDS_URL, ai_token, headers=key, order_id=order.pk)
    refund_id = first.json()['refund']['id']
    client.post(f'{REFUNDS_URL}{refund_id}/approve/', **auth(owner_token))

    later = timezone.now() + timedelta(days=8)
    with mock.patch('orders.models.timezone.now', return_value=later):
        again = post(client, REFUNDS_URL, ai_token, headers=key, order_id=order.pk)

    body = again.json()
    assert again.status_code == 202, f'그때의 판정(ESCALATE)의 코드여야 한다: {body}'
    assert body['outcome'] == REPLAYED
    assert body['rule_ids'] == [REFUND_002], '기한 초과로 재판정하면 안 된다.'
    assert body['refund']['id'] == refund_id
    assert body['refund']['status'] == Refund.Status.APPROVED, '객체 상태는 지금 값이다.'


@pytest.mark.django_db
def test_열쇠_없이_기한이_지난_뒤_다시_신청하면_지금_상태를_듣는다(world, ai):
    """열쇠가 없어도 마찬가지다 — 살아 있는 환불이 신규 판정보다 앞에 있다.

    돌아오는 것은 `ALREADY` + 지금 상태이지 `REFUND-001@v1` 거부가 아니다.
    """
    order = Order.objects.get(order_number='SEED-0003')
    Refund.apply(order, 30_000, '사이즈 불일치', ai)

    later = timezone.now() + timedelta(days=10)
    with mock.patch('orders.models.timezone.now', return_value=later):
        verdict, outcome = services.propose_refund(ai, order, 30_000, '다시 보냄')

    assert outcome.state == Outcome.ALREADY
    assert REFUND_001 not in verdict.rule_ids, '이미 환불된 건을 기한 초과로 거부하면 안 된다.'
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
        return response.status_code, response.json().get('rule_ids', [])

    results = _race(worker, Refund, 'approve')

    codes = sorted(code for code, _ in results)
    assert codes == [200, 409], f'하나는 확정, 하나는 조건 불일치여야 한다: {results}'
    loser = [rules for code, rules in results if code == 409][0]
    assert REFUND_003 in loser, f'패자도 규칙 ID 를 들고 돌아와야 한다: {loser}'
    refund = Refund.objects.get(pk=refund_pk)
    assert refund.status == Refund.Status.APPROVED
    assert refund.decided_via == Refund.Via.OWNER
    assert Refund.objects.filter(decided_at__isnull=False).count() == 1, (
        '패자가 결정 시각을 덮어쓰면 안 된다 — 확정한 사건은 하나다.'
    )


@pytest.mark.django_db
def test_거부한_뒤_승인도_막힌다(world, ai, owner):
    """계약은 방향을 가리지 않는다 — 확정은 **제안 상태에서만**이다.

    승인 뒤 거부만 막고 거부 뒤 승인을 열어 두면, 점주가 순서만 바꿔 누르면
    같은 거짓말이 생긴다.
    """
    order = Order.objects.get(order_number='SEED-0002')
    _, outcome = Refund.apply(order, 54_000, '사이즈 불일치', ai)
    refund = outcome.refund
    refund.reject(owner, note='재고 확인함')

    with pytest.raises(InvalidTransition) as caught:
        refund.approve(owner)

    assert REFUND_003 in caught.value.verdict.rule_ids
    refund.refresh_from_db()
    assert refund.status == Refund.Status.REJECTED
    order.refresh_from_db()
    assert order.status == Order.Status.PAID, '거부된 건이 주문을 취소시키면 안 된다.'


@pytest.mark.django_db
def test_콘솔에서_승인을_두_번_누르면_경고가_뜬다(world, ai, owner):
    """같은 계약을 HTML 화면도 받는다 — 다만 표현이 다르다.

    409 화면은 AI 직원이 읽을 판정용이고, 버튼을 두 번 누른 점주에게 필요한 것은
    "이미 지나간 일"이라는 한 줄이다. 그래서 `messages.warning` 이다.
    """
    order = Order.objects.get(order_number='SEED-0002')
    _, outcome = Refund.apply(order, 54_000, '사이즈 불일치', ai)
    url = f'/orders/refunds/{outcome.refund.pk}/approve/'
    client = Client()
    assert client.login(username='owner', password='owner1234')

    client.post(url)
    second = client.post(url, follow=True)

    texts = [str(message) for message in second.context['messages']]
    assert any(REFUND_003 in text for text in texts), f'경고에 규칙 ID 가 있어야 한다: {texts}'
    assert Refund.objects.get(pk=outcome.refund.pk).status == Refund.Status.APPROVED


@pytest.mark.django_db
def test_admin_액션은_확정할_수_있는_것만_확정하고_건수를_나눠_말한다(world, ai, owner):
    """승인 큐에서 여러 건을 한 번에 고르면, 그중 이미 지나간 건이 섞일 수 있다.

    액션 전체를 실패시키면 나머지 건까지 못 넘어간다. 한 건의 조건 불일치는
    그 건의 사정이므로, 건너뛴 건수를 세어 점주에게 말해 준다.
    """
    queued = Refund.apply(
        Order.objects.get(order_number='SEED-0002'), 54_000, '대기 중', ai
    )[1].refund
    settled = Refund.apply(
        Order.objects.get(order_number='SEED-0003'), 30_000, '이미 확정', ai
    )[1].refund
    assert settled.status == Refund.Status.APPROVED
    client = Client()
    assert client.login(username='owner', password='owner1234')

    response = client.post(
        '/admin/orders/refund/',
        {
            'action': 'approve_selected',
            '_selected_action': [str(queued.pk), str(settled.pk)],
            'index': '0',
        },
        follow=True,
    )

    texts = [str(message) for message in response.context['messages']]
    assert '1건 승인, 1건은 제안 상태가 아니어서 건너뜀.' in texts, texts
    queued.refresh_from_db()
    assert queued.status == Refund.Status.APPROVED


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


@pytest.mark.django_db
def test_스키마_오류는_잠금이_아니다_500(world, ai):
    """`OperationalError` 라는 것만으로는 "다시 보내라"의 근거가 못 된다.

    `no such table: busy_orders` 도 `OperationalError` 이고 메시지에 `busy` 가
    들어 있다. 부분 문자열로 세면 **스키마 오류를 재시도 가능한 잠금으로
    번역**하게 되고, 클라이언트는 영원히 다시 보낸다. 그래서 판별은 SQLite
    오류 코드(`SQLITE_BUSY`·`SQLITE_LOCKED`)를 먼저 보고, 코드가 없을 때만
    알려진 **정확한 메시지**와 대조한다.
    """
    _, token = APIToken.issue(ai, 'live')
    order = Order.objects.get(order_number='SEED-0004')
    client = Client(raise_request_exception=False)

    with mock.patch(
        'orders.services.pay_order', side_effect=OperationalError('no such table: busy_orders')
    ):
        response = client.post(f'{ORDERS_URL}{order.pk}/pay/', **auth(token))

    assert response.status_code == 500, '고장은 고장이다 — 503 으로 감싸면 안 된다.'


# --- 5. 실측: 잠갔다고 믿는 잠금 · 준비 확인 -------------------------------------


@pytest.mark.django_db
def test_select_for_update는_SQLite에서_아무것도_잠그지_않는다(world):
    """`select_for_update()` 는 오류도 경고도 없이 **무시된다**.

    Django 는 백엔드가 지원할 때만 `FOR UPDATE` 를 붙인다. SQLite 는
    `has_select_for_update = False` 라 그냥 평범한 SELECT 가 나간다.
    "잠갔다고 믿는데 안 잠긴 잠금"이 이 단계에서 실측하는 것 하나다.

    이 시험이 말하는 것은 **`FOR UPDATE` 행 잠금이 붙지 않는다**까지다.
    "아무 잠금도 없다"가 아니다 — 트랜잭션 자체의 쓰기 잠금(`IMMEDIATE`)은
    따로 걸리고, 그건 행 단위가 아니라 DB 단위다. 둘은 다른 층이다.

    PostgreSQL 이면 진짜 행 잠금이 걸린다. 그건 8단계 전에는 확인할 수 없다
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


def _row(ledger, rule_id):
    """대장에서 그 ID 의 행 하나를 찾아 셀 목록으로 돌려준다."""
    rows = [line for line in ledger.splitlines() if line.startswith(f'| `{rule_id}`')]
    assert len(rows) == 1, f'{rule_id} 행이 {len(rows)}개다 — 대장의 ID 는 유일해야 한다.'
    return [cell.strip() for cell in rows[0].strip('|').split('|')]


def test_규칙_대장에_REFUND_003_행이_있다():
    """새 법은 대장에 남는다. 코드만 고치고 대장을 안 쓰면 반쪽이다."""
    cells = _row(_ledger(), REFUND_003)

    assert cells[1] == REFUND_003_TEXT, '점주가 말한 원문 그대로여야 한다.'
    assert cells[6] == 'active'
    assert 'Refund.apply' in cells[7], '멱등 면의 적용 경로가 한정돼 있어야 한다.'
    assert '평생 한 번' in cells[7], 'DB 가 지키는 범위를 한정해 적어야 한다.'


def test_4단계_세_행은_ID_원문_출처까지_그대로다():
    """대장은 **삭제 금지·내용 보존** 대장이다.

    원문이 문서 어딘가에 있는지만 보면 부족하다 — 행이 통째로 사라지고
    본문에 문장만 남아 있어도 통과해 버린다. 그래서 기준 태그(`stage-04-done`)
    시점의 **ID·원문·출처 세 칸**을 고정값으로 박아 두고 비교한다.
    바꿔야 할 상황이면 그건 새 버전(`@v2`)이지 덮어쓰기가 아니다.
    """
    ledger = _ledger()

    for rule_id, (text, source) in STAGE_04_ROWS.items():
        cells = _row(ledger, rule_id)
        assert cells[0] == f'`{rule_id}`'
        assert cells[1] == text, f'{rule_id} 의 원문이 바뀌었다.'
        assert cells[2] == source, f'{rule_id} 의 출처가 바뀌었다 — 배후의 사람이 사라진다.'
        assert RULE_TEXTS[rule_id] == text, '코드의 원문과 대장의 원문이 갈라졌다.'
