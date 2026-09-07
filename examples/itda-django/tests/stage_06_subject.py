"""6단계 — 주체: 결제 페이지가 묻는 세 질문 — 누구·언제·(그리고 4단계의) 어디로.

앞 둘이 이 단계의 법이다.

    누구의 것인가   SCOPE-001@v1   `get_object_or_404(Order, pk=pk, user=request.user)` → 404
    언제까지인가    PAY-001@v1     서명 토큰 + 1시간 → 409
    어디로 갈 수 있나  ORDER-001@v1  4단계의 전이 계약 — 그대로다

시작 상태(`stage-06-start`)에서 15개 중 **7개가 실패**한다. 통과하는 여덟은
채점이 아니라 **준비 확인**(고객 로그인·결제 페이지·링크 발급 API·도구 핸들)과
**4단계 계약의 회귀**다. 그중 8·13 은 특히 눈여겨볼 것 — 시작 상태에서 통과하는
이유가 "법이 켜져 있어서"가 아니다.

- 8(`정확히 한 시간은 유효하다`) — 시작 상태의 링크는 **항상** 산다. 경계에서
  통과하는 것이 아니라 경계가 없다.
- 13(`두 탭에서 동시에 결제`) — 결제 경합은 4단계 계약이 이미 버틴다. 이 단계가
  더하는 것은 경합이 아니라 **주체와 시간**이다.

## 시계는 하나만 돌린다

만료 시험은 `PaymentLinkTokenGenerator._now` **하나만** 패치한다(원문 주석
"Used for mocking in tests" 그대로). `timezone.now` 를 건드리면 `expires_at` 과
`created_at` 이 같이 흔들려 무엇을 쟀는지 흐려진다. 발급은 먼저 하고, **검사
시점의 시계만** 미룬다.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import APIToken
from agent.live.tools import toolset
from django_itda.models import ToolCall
from orders import services
from orders.models import Order
from orders.rules import ORDER_001, REFUND_001, REFUND_002, REFUND_003, RULE_TEXTS
from orders.verdict import Outcome, Verdict
from shop.models import Product

User = get_user_model()

ORDERS_URL = '/api/orders/'

# 시작 상태에는 아직 없는 이름들이다. 여기서 죽으면 파일 전체가 수집조차 안 돼
# '무엇이 몇 개 실패했는지'가 안 보이므로, 기대값을 문자열로 들고 있는다.
try:
    from orders.rules import PAY_001, PAY_LINK_TIMEOUT, SCOPE_001
except ImportError:  # 6단계 시작 상태
    PAY_001, SCOPE_001, PAY_LINK_TIMEOUT = 'PAY-001@v1', 'SCOPE-001@v1', 60 * 60

try:
    from orders.tokens import PaymentLinkTokenGenerator, payment_token
except ImportError:  # 6단계 시작 상태 — 돌릴 시계 자체가 없다
    PaymentLinkTokenGenerator = payment_token = None

PAY_001_TEXT = (
    '결제는 네가 하지 마라. 고객에게 결제 링크를 안내하고 고객이 직접 결제하게 하라. '
    '링크는 발급 후 1시간 안에만 유효하다.'
)
SCOPE_001_TEXT = '고객의 주문은 그 고객에게만 보여 줘라.'

# `stage-05-done` 시점 대장 네 행의 ID·원문·출처. 고정값이다 — 여기를 고쳐야
# 테스트가 통과한다면, 그건 append-only 대장을 덮어썼다는 뜻이다.
STAGE_05_ROWS = {
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
    REFUND_003: (
        '같은 환불을 두 번 처리하지 마라. 한 주문에 환불은 한 번이다.',
        '`agent/prompts/ai_staff.md` 4번 (점주, 2026-09-06)',
    ),
}


# --- 도구 ----------------------------------------------------------------------


@pytest.fixture
def ai(world):
    return User.objects.get(username='ai-staff')


@pytest.fixture
def seed4(world):
    """SEED-0004 — 결제 대기, 한정판 흑임자 다쿠아즈 1개. **주문자는 bob 이다.**"""
    order = Order.objects.get(order_number='SEED-0004')
    assert order.user.username == 'bob', '채점표의 주문자는 bob, 침입자는 alice 다.'
    return order


def issue(order, actor=None):
    """AI 직원의 문으로 결제 링크를 하나 받는다. `(경로, payment)`.

    URL 이 아니라 **경로**를 돌려주는 것은 테스트 클라이언트가 두드릴 자리라서다.
    링크의 모양(토큰이 붙었는가)은 테스트 5 가 따로 본다.
    """
    actor = actor or User.objects.get(username='ai-staff')
    _verdict, payment = services.issue_payment_link(actor, order)
    assert payment is not None, '결제 대기 주문에는 링크가 나와야 한다.'
    return urlparse(payment['url']).path, payment


def login(username):
    client = Client()
    assert client.login(username=username, password='pass1234')
    return client


def token_now():
    """토큰 생성기의 시계를 읽는다. 시작 상태에는 생성기가 없다."""
    return payment_token._now() if payment_token is not None else datetime.now()


@contextmanager
def clock(at):
    """`PaymentLinkTokenGenerator._now` **하나만** 돌린다.

    시작 상태에는 생성기가 없다. 그때는 아무것도 패치하지 않는다 — 시계를 못
    돌린 것이 아니라 **돌릴 시계가 없는 것**이고, 그게 오늘의 결함이다.
    """
    if PaymentLinkTokenGenerator is None:
        yield
        return
    with mock.patch.object(PaymentLinkTokenGenerator, '_now', return_value=at):
        yield


# --- 1. 누구의 것인가 — SCOPE-001@v1 --------------------------------------------


@pytest.mark.django_db
def test_남의_주문_결제_페이지는_404(seed4):
    """alice 가 bob 의 결제 링크를 연다. 로그인은 했다 — 자기 계정으로.

    **404 다. 403 이 아니다.** 403 은 "있는데 너는 못 본다"이고, 404 는
    "네 세계에는 없다"이다. 남의 주문이 있다는 사실 자체가 정보다.
    """
    path, _ = issue(seed4)

    response = login('alice').get(path)

    assert response.status_code == 404, '링크는 열쇠가 아니다 — 열쇠는 주인도 묻는다.'
    assert seed4.recipient_name not in response.content.decode()


@pytest.mark.django_db
def test_남의_주문은_결제도_못_한다(seed4):
    """읽기만 막고 쓰기를 열어 두면 반쪽이다. 같은 한 줄이 둘 다 막는다."""
    path, _ = issue(seed4)
    product = Product.objects.get(name='한정판 흑임자 다쿠아즈')

    response = login('alice').post(path)

    assert response.status_code == 404
    seed4.refresh_from_db()
    assert seed4.status == Order.Status.PENDING, 'alice 가 bob 의 주문을 결제하면 안 된다.'
    assert Product.objects.get(pk=product.pk).stock == 1, '재고도 그대로여야 한다.'


@pytest.mark.django_db
def test_주문자는_결제_페이지를_연다(seed4):
    """막는 것만 확인하면 반쪽이다 — 주인은 열려야 한다."""
    path, _ = issue(seed4)

    response = login('bob').get(path)

    assert response.status_code == 200
    body = response.content.decode()
    assert seed4.order_number in body
    assert str(seed4.total_amount) in body


@pytest.mark.django_db
def test_로그인_없이는_고객_로그인으로(seed4):
    """비로그인은 콘솔 로그인(`LOGIN_URL`)이 아니라 **고객 로그인**으로 간다.

    `LOGIN_URL` 은 AI 직원 콘솔의 것이라 바꾸지 않았다. 결제 페이지만 자기
    로그인 자리를 명시한다 — 고객에게 서비스 계정 로그인 화면을 보여 줄 수는 없다.
    """
    path, _ = issue(seed4)

    response = Client().get(path)

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


# --- 2. 링크 발급 — AI 의 문은 여기까지 -----------------------------------------


@pytest.mark.django_db
def test_링크_발급은_격상이고_규칙_ID가_붙는다(ai, seed4):
    """격상은 실패가 아니라 **사람에게 넘어간 상태**다. 이번엔 그 사람이 고객이다.

    `expires_at` 은 **정보용**이다. 정본은 토큰이고, 이 값은 화면에 보여 줄 숫자다.
    """
    verdict, payment = services.issue_payment_link(ai, seed4)

    assert verdict.kind == Verdict.ESCALATE
    assert PAY_001 in verdict.rule_ids, '법이 켜졌으면 판정에 규칙 ID 가 실린다.'
    segments = [part for part in urlparse(payment['url']).path.split('/') if part]
    assert len(segments) == 3, f'링크에 서명 토큰 세그먼트가 있어야 한다: {payment["url"]}'
    assert payment['expires_at'], '만료 시각이 있어야 한다.'
    expected = timezone.now() + timedelta(seconds=PAY_LINK_TIMEOUT)
    gap = abs((datetime.fromisoformat(payment['expires_at']) - expected).total_seconds())
    assert gap < 5, f'만료는 발급 시점 + {PAY_LINK_TIMEOUT}초여야 한다 (오차 {gap}초).'

    seed4.refresh_from_db()
    assert seed4.status == Order.Status.PENDING, '링크만으로는 세계가 안 움직인다.'


@pytest.mark.django_db
def test_결제_대기가_아니면_링크를_발급하지_않는다(ai, world):
    """`mark_paid` 를 막던 것과 **같은 규칙 ID** 가 한 자리 앞에서 답한다."""
    order = Order.objects.get(order_number='SEED-0002')

    verdict, payment = services.issue_payment_link(ai, order)

    assert verdict.kind == Verdict.DENY
    assert verdict.rule_ids == [ORDER_001]
    assert payment is None, '링크조차 나가면 안 된다.'
    assert verdict.alternatives


# --- 3. 언제까지인가 — PAY-001@v1 ------------------------------------------------


@pytest.mark.django_db
def test_한_시간_일_초_뒤_링크는_닫힌다(seed4):
    """URL 은 복사되고 전달되고 남는다. 어제 준 링크가 오늘도 열리면 안 된다."""
    issued_at = token_now()
    with clock(issued_at):
        path, _ = issue(seed4)
    client = login('bob')

    with clock(issued_at + timedelta(seconds=PAY_LINK_TIMEOUT + 1)):
        response = client.get(path)

    assert response.status_code == 409
    body = response.content.decode()
    assert PAY_001 in body
    assert '새 결제 링크' in body, '주문이 아직 결제 대기니 갈 수 있는 길이 있다.'


@pytest.mark.django_db
def test_정확히_한_시간은_유효하다(seed4):
    """경계는 `>` 이지 `>=` 가 아니다 — "1시간 안에만 유효하다"의 안쪽이다.

    시작 상태에서도 통과한다. 경계에서 통과하는 것이 아니라 **경계가 없다.**
    """
    issued_at = token_now()
    with clock(issued_at):
        path, _ = issue(seed4)
    client = login('bob')

    with clock(issued_at + timedelta(seconds=PAY_LINK_TIMEOUT)):
        response = client.get(path)

    assert response.status_code == 200


@pytest.mark.django_db
def test_결제된_주문의_링크는_스스로_닫힌다(seed4):
    """토큰 해시에 **주문 상태**가 섞여 있다 — 상태가 바뀌면 같은 토큰이 죽는다.

    비밀번호 재설정 토큰에서 `user.password` 가 하던 역할을 `order.status` 가 한다.
    그리고 대안이 달라진다 — 결제가 끝난 주문에 "새 링크를 요청하라"는 없는 길이다.

    **만료는 이중 결제를 막는 장치가 아니다.** 그건 `ORDER-001@v1` 의 몫이고
    그대로다. 토큰이 먼저 닫고, 계약이 마지막에 지킨다 — 두 겹이다.
    """
    path, _ = issue(seed4)
    client = login('bob')

    paid = client.post(path)
    assert paid.status_code == 200

    response = client.get(path)

    assert response.status_code == 409
    body = response.content.decode()
    assert PAY_001 in body
    assert '새 결제 링크' not in body, '결제가 끝난 주문에 새 링크는 없는 길이다.'
    assert '현재 상태' in body


# --- 4. 4단계 계약의 회귀 — 결제 자체는 그대로다 ---------------------------------


@pytest.mark.django_db
def test_결제하면_주문이_paid_되고_재고가_깎인다(seed4):
    """주체가 바뀌어도 계약은 그대로다. 부르는 사람만 달라졌다."""
    path, _ = issue(seed4)
    product = Product.objects.get(name='한정판 흑임자 다쿠아즈')

    response = login('bob').post(path)

    assert response.status_code == 200
    seed4.refresh_from_db()
    assert seed4.status == Order.Status.PAID
    assert Product.objects.get(pk=product.pk).stock == 0


@pytest.mark.django_db
def test_AI_직원의_결제_API는_링크만_준다(client, ai, seed4):
    """준비 확인 — 시작 상태에서 이미 "결제는 고객이" 다."""
    _, raw = APIToken.issue(ai, 'stage-06')
    product = Product.objects.get(name='한정판 흑임자 다쿠아즈')

    response = client.post(
        f'{ORDERS_URL}{seed4.pk}/pay/', HTTP_AUTHORIZATION=f'Bearer {raw}'
    )
    body = response.json()

    assert response.status_code == 202
    assert body['outcome'] == Outcome.QUEUED
    assert body['payment']['url']
    seed4.refresh_from_db()
    assert seed4.status == Order.Status.PENDING
    assert Product.objects.get(pk=product.pk).stock == 1


@pytest.mark.django_db
def test_도구_pay_order는_핸들에_URL을_싣는다(ai, seed4):
    """승인 핸들이 "누른다"에서 "연다"로 일반화된 자리(django-itda v0.1.1).

    패키지가 얹는 폴링 필드 위에 도구가 `url`·`expires_at` 을 보탠다.
    최상위 `handle` 은 언제나 **한 dict** 다.
    """
    result = toolset.call('pay_order', ai, via=ToolCall.Via.MCP, order_id=seed4.pk)

    assert result['kind'] == Verdict.ESCALATE
    assert set(result['handle']) == {'check_tool', 'id', 'status', 'url', 'expires_at'}
    assert result['handle']['check_tool'] == 'get_order'
    assert result['handle']['id'] == seed4.pk
    assert result['handle']['status'] == Order.Status.PENDING
    assert result['handle']['url'] == result['payment']['url']


@pytest.mark.django_db(transaction=True)
def test_두_탭에서_동시에_결제하면_하나만_통과한다(world, ai):
    """**시작 상태에서도 통과한다.** 결제 경합은 4단계 계약이 이미 버틴다.

    이 단계가 더하는 것은 경합이 아니라 **주체와 시간**이다. 패자의 규칙 ID 는
    `ORDER-001@v1` 또는 `PAY-001@v1` — 토큰 검사와 전이 계약 중 어느 겹에서
    걸렸는지는 타이밍이 정하고, 둘 다 정직한 답이다.

    5단계의 `_race` 헬퍼를 그대로 쓴다. 경합을 재현 가능하게 만드는 값(파일
    테스트 DB · 트랜잭션 밖의 Barrier)이 거기 다 적혀 있다.
    """
    from stage_05_contention import _race

    order = Order.objects.get(order_number='SEED-0004')
    product = Product.objects.get(name='한정판 흑임자 다쿠아즈')
    assert product.stock == 1
    path, _ = issue(order, actor=ai)

    def worker(_index):
        client = login('bob')
        response = client.post(path)
        return response.status_code, response.content.decode()

    results = _race(worker, Order, 'mark_paid')

    assert sorted(code for code, _ in results) == [200, 409], f'{[c for c, _ in results]}'
    loser = [body for code, body in results if code == 409][0]
    assert ORDER_001 in loser or PAY_001 in loser
    assert Product.objects.get(pk=product.pk).stock == 0, '재고는 한 번만 깎인다.'


# --- 5. 대장과 프롬프트 ---------------------------------------------------------


def _ledger():
    return (Path(__file__).resolve().parent.parent / 'RULES.md').read_text(encoding='utf-8')


def _row(ledger, rule_id):
    rows = [line for line in ledger.splitlines() if line.startswith(f'| `{rule_id}`')]
    assert len(rows) == 1, f'{rule_id} 행이 {len(rows)}개다 — 대장의 ID 는 유일해야 한다.'
    return [cell.strip() for cell in rows[0].strip('|').split('|')]


def test_규칙_대장에_두_행이_있고_앞_행은_그대로다():
    """새 법 둘은 대장에 남고, 앞 네 행은 ID·원문·출처까지 그대로여야 한다."""
    ledger = _ledger()

    for rule_id, text in ((PAY_001, PAY_001_TEXT), (SCOPE_001, SCOPE_001_TEXT)):
        cells = _row(ledger, rule_id)
        assert cells[1] == text, f'{rule_id} 의 원문은 점주가 말한 그대로여야 한다.'
        assert cells[6] == 'active'

    assert '고객 문' in _row(ledger, SCOPE_001)[7], (
        'SCOPE-001 의 보장 범위는 고객 문까지다 — 그 한정을 대장에 적어야 한다.'
    )

    for rule_id, (text, source) in STAGE_05_ROWS.items():
        cells = _row(ledger, rule_id)
        assert cells[0] == f'`{rule_id}`'
        assert cells[1] == text, f'{rule_id} 의 원문이 바뀌었다.'
        assert cells[2] == source, f'{rule_id} 의 출처가 바뀌었다 — 배후의 사람이 사라진다.'
        assert RULE_TEXTS[rule_id] == text, '코드의 원문과 대장의 원문이 갈라졌다.'


def test_프롬프트_5_6번은_이사됐고_6번은_부분이다():
    """이사 표시는 지우기 위한 것이 아니라 **대조하기 위한** 것이다.

    6번은 **부분 이사**다 — 고객의 문(결제 페이지)에서만 지켜지고 AI 직원의 문
    (`list_orders`)에서는 여전히 모든 고객의 주문이 보인다. 그 '부분'이 8단계
    부채 리포트의 첫 실물이라 표시에 그대로 적는다.
    """
    prompt = (
        Path(__file__).resolve().parent.parent / 'agent' / 'prompts' / 'ai_staff.md'
    ).read_text(encoding='utf-8')
    lines = prompt.splitlines()

    fifth = next(index for index, line in enumerate(lines) if line.startswith('5. '))
    sixth = next(index for index, line in enumerate(lines) if line.startswith('6. '))

    assert PAY_001_TEXT in lines[fifth], '원문은 지우지 않는다.'
    assert SCOPE_001_TEXT in lines[sixth]
    assert f'코드로 이사됨 (`{PAY_001}' in lines[fifth + 1]
    assert SCOPE_001 in lines[sixth + 1]
    assert '미이사' in lines[sixth + 1], '부분 이사를 통째 이사로 적으면 부채가 사라진다.'
