"""7단계 — 기록: 장부의 세 질문 — 사실 뒤에 적는가, 같은 트랜잭션인가, 고칠 수 없는가.
그리고 `paid_at` 의 원가.

시작 상태(`stage-07-start`)에서 18개 중 **16개가 실패**한다. 통과하는 둘(12·13)은
채점이 아니라 **준비 확인**이다 — 호출 문맥(django-itda v0.2)과 모델 층의 거부는
시작 상태에 이미 있다. 없는 것은 셋이다.

    (a) `mark_paid()` 가 누가 결제했는지 받지 않는다
    (b) 장부가 **사실 전에, 트랜잭션 밖에서** 적힌다 — 의도를 사실로 적는다
    (c) `Order.paid_at` 이 없다 — 4단계의 교육상 가정이 아직 가정이다

## 장부는 무엇을 세는가

이 파일의 단언은 대부분 **행 수**다. 장부가 거짓말하는 방식이 "없는 행을
만드는 것"과 "있어야 할 행이 없는 것" 둘뿐이기 때문이다. 세계의 상태(주문
상태·재고)를 같이 세는 것도 그래서다 — 세계가 한 번 움직였는데 장부에 두
줄이 있으면, 둘 중 하나는 거짓이다.
"""

from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from django_itda.models import ToolCall
from ledger.models import Event, LedgerImmutable
from orders import services
from orders.models import InsufficientStock, Order, Refund
from orders.rules import ORDER_001, PAY_001, REFUND_001, REFUND_002, REFUND_003, SCOPE_001
from orders.verdict import Verdict
from shop.models import Product

User = get_user_model()

# 시작 상태에는 아직 없는 이름이다. 여기서 죽으면 파일 전체가 수집조차 안 돼
# '무엇이 몇 개 실패했는지'가 안 보인다(6단계 채점표와 같은 규율).
try:
    from orders.rules import LEDGER_001
except ImportError:  # 7단계 시작 상태
    LEDGER_001 = 'LEDGER-001@v1'

LEDGER_001_TEXT = (
    '세계를 움직인 일은 빠짐없이 장부에 남겨라 — 누가·언제·어느 문으로·어떤 규칙으로. '
    '장부는 고치지 마라.'
)

#: `stage-06-done` 시점 대장 여섯 행의 ID·원문·출처. 고정값이다 — 여기를 고쳐야
#: 테스트가 통과한다면, 그건 append-only 대장을 덮어썼다는 뜻이다.
STAGE_06_ROWS = {
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
    PAY_001: (
        '결제는 네가 하지 마라. 고객에게 결제 링크를 안내하고 고객이 직접 결제하게 하라. '
        '링크는 발급 후 1시간 안에만 유효하다.',
        '`agent/prompts/ai_staff.md` 5번 (점주, 2026-09-07)',
    ),
    SCOPE_001: (
        '고객의 주문은 그 고객에게만 보여 줘라.',
        '`agent/prompts/ai_staff.md` 6번 (점주, 2026-09-07)',
    ),
}


# --- 세계 ----------------------------------------------------------------------


@pytest.fixture
def ai(world):
    return User.objects.get(username='ai-staff')


@pytest.fixture
def bob(world):
    return User.objects.get(username='bob')


@pytest.fixture
def owner(world):
    return User.objects.get(username='owner')


@pytest.fixture
def seed4(world):
    """SEED-0004 — 결제 대기, 한정판 흑임자 다쿠아즈 1개. 주문자는 **bob** 이다."""
    return Order.objects.get(order_number='SEED-0004')


def order(number):
    return Order.objects.get(order_number=number)


def events(transition=None, **filters):
    rows = Event.objects.all()
    if transition is not None:
        rows = rows.filter(transition=transition)
    return list(rows.filter(**filters))


def issue(order, actor):
    """AI 직원의 문으로 결제 링크를 받는다 — 테스트 클라이언트가 두드릴 경로."""
    _verdict, payment = services.issue_payment_link(actor, order)
    assert payment is not None, '결제 대기 주문에는 링크가 나와야 한다.'
    return urlparse(payment['url']).path


def login(username, password='pass1234'):
    client = Client()
    assert client.login(username=username, password=password)
    return client


def limited():
    return Product.objects.get(name='한정판 흑임자 다쿠아즈')


# --- 1. 사실 뒤에 적는가 --------------------------------------------------------


@pytest.mark.django_db
def test_거부된_결제는_장부에_없다(ai, world):
    """세계가 안 움직였으면 도메인 장부에 남을 것이 없다.

    거부된 **호출**이 있었다는 사실은 도구면 궤적(`ToolCall`)의 몫이다. 층이
    다르다 — 여기는 "세계가 무엇으로 바뀌었나" 만 적는다.
    """
    paid = order('SEED-0002')

    verdict = services.pay_order(ai, paid)

    assert verdict.kind == Verdict.DENY
    assert verdict.rule_ids == [ORDER_001]
    assert events(Event.Transition.ORDER_PAID) == [], (
        '거부된 결제가 장부에 남으면, 장부는 세계가 아니라 의도를 적은 것이다.'
    )


@pytest.mark.django_db
def test_재고_부족이면_차감_기록도_없다(ai, bob, world):
    """한정판 하나를 둘이 산다. 세계는 한 번 깎이고, 장부에도 한 줄이어야 한다."""
    product = limited()
    first = order('SEED-0004')
    second = services.intake_order(bob, [(product, 1)], {
        'recipient_name': '박밥', 'phone': '010-0000-0002', 'address': '부산시 가상구 없는동 2-2',
    })

    services.pay_order(ai, first)
    with pytest.raises(InsufficientStock):
        services.pay_order(ai, second)

    assert Product.objects.get(pk=product.pk).stock == 0
    assert len(events(Event.Transition.STOCK_DEDUCTED)) == 1, (
        '차감되지 않은 재고를 차감했다고 적으면 장부가 rowcount 를 무시한 것이다.'
    )
    second.refresh_from_db()
    assert second.status == Order.Status.PENDING
    assert len(events(Event.Transition.ORDER_PAID)) == 1


@pytest.mark.django_db
def test_결제_사실은_누가_언제_어느_문으로(ai, seed4):
    """고객이 자기 손으로 결제한다 — 그 사실이 자리·문·시각과 함께 남는가."""
    path = issue(seed4, ai)

    response = login('bob').post(path)

    assert response.status_code == 200
    rows = events(Event.Transition.ORDER_PAID)
    assert len(rows) == 1
    paid = rows[0]
    assert paid.actor is not None and paid.actor.username == 'bob'
    assert paid.actor_label == 'bob', '계정이 지워져도 이름은 남아야 한다.'
    assert paid.door == Event.Door.CUSTOMER, '어느 문으로 들어왔는지가 발견 3 의 답이다.'
    assert paid.kind == Event.Kind.ALLOW
    assert paid.before == {'status': Order.Status.PENDING}
    assert paid.after['status'] == Order.Status.PAID
    assert paid.after['paid_at'], '결제 시각이 사실과 함께 남아야 한다.'
    assert len(paid.call_id) == 32, '요청 하나 = 호출 하나.'
    assert paid.subject == Order.objects.get(pk=seed4.pk)


# --- 2. `paid_at` 의 원가 -------------------------------------------------------


@pytest.mark.django_db
def test_paid_at은_상태와_같은_문장에서_채워진다(bob, seed4):
    """상태 UPDATE 와 **같은 문장**이다. 두 문장으로 나누면 그 사이가 거짓이 된다."""
    before = timezone.now()

    seed4.mark_paid(bob)

    seed4.refresh_from_db()
    assert seed4.status == Order.Status.PAID
    assert seed4.paid_at is not None
    assert seed4.paid_at >= before


@pytest.mark.django_db
def test_시드된_결제_주문은_backfill_로_paid_at을_가진다(world):
    """4단계가 "결제일은 `created_at` 으로 본다" 고 적어 둔 가정이 데이터가 된다."""
    for number in ('SEED-0001', 'SEED-0002', 'SEED-0003'):
        row = order(number)
        assert row.paid_at == row.created_at, f'{number} 의 결제 시각이 비어 있다.'
    assert order('SEED-0004').paid_at is None, '결제 대기 주문에는 결제 시각이 없다.'


@pytest.mark.django_db
def test_결제_완료인데_결제_시각이_없으면_DB가_거절한다(world):
    """선언 한 줄(`CheckConstraint`)이 지키는 것 — 경로를 지나지 않아도 걸린다."""
    paid = order('SEED-0002')

    # `atomic()` 으로 감싸는 것은 5단계 규율 그대로다 — 무결성 오류가 난 뒤의
    # 트랜잭션은 깨져 있어서, 감싸지 않으면 **이 뒤의 모든 쿼리**가 죽는다.
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=paid.pk).update(paid_at=None)


@pytest.mark.django_db
def test_취소_주문의_결제_시각은_모름으로_남는다(world):
    """장부가 없던 시절의 취소는 결제 뒤인지 결제 전인지 **알 수 없다.**

    모름을 모름으로 둔다. 제약은 취소를 묻지 않는다 — 둘 다 통과한다.
    """
    after_payment = order('SEED-0002')
    Order.objects.filter(pk=after_payment.pk).update(status=Order.Status.CANCELLED)
    after_payment.refresh_from_db()
    assert after_payment.paid_at is not None, '결제 뒤 취소는 결제 시각을 지우지 않는다.'

    before_payment = order('SEED-0004')
    Order.objects.filter(pk=before_payment.pk).update(status=Order.Status.CANCELLED)
    before_payment.refresh_from_db()
    assert before_payment.paid_at is None, '결제 전 취소는 결제 시각이 없어도 된다.'


# --- 3. 판정 각인 ---------------------------------------------------------------


@pytest.mark.django_db
def test_제안에는_판정이_각인된다(ai, world):
    """`decisive_rules[]` — **어떤 규칙이** 이 사실을 격상했나."""
    target = order('SEED-0002')

    services.propose_refund(ai, target, target.total_amount, '고객 요청')

    rows = events(Event.Transition.REFUND_PROPOSED)
    assert len(rows) == 1
    assert rows[0].kind == Event.Kind.ESCALATE
    assert rows[0].rule_ids == [REFUND_002], '판정 각인이 없으면 "왜" 가 사라진다.'
    assert rows[0].actor == ai


@pytest.mark.django_db
def test_거부_재전송_기존_건은_새_행이_아니다(ai, world):
    """세 갈래 다 세계를 바꾸지 않는다 — 도메인 장부에 새 행이 없다."""
    denied = order('SEED-0001')
    verdict, _outcome = services.propose_refund(ai, denied, 10000, '고객 요청')
    assert verdict.kind == Verdict.DENY
    assert events(Event.Transition.REFUND_PROPOSED) == [], '거부는 사실이 아니다.'

    target = order('SEED-0002')
    services.propose_refund(ai, target, target.total_amount, '고객 요청', 'key-1')
    services.propose_refund(ai, target, target.total_amount, '고객 요청', 'key-1')
    services.propose_refund(ai, target, target.total_amount, '고객 요청', 'key-2')

    assert len(events(Event.Transition.REFUND_PROPOSED)) == 1, (
        '재전송(REPLAYED)·기존 건(ALREADY)은 새 사실이 아니다 — 새 행도 없다.'
    )


@pytest.mark.django_db
def test_admin_승인은_장부에_남는다_LogEntry는_여전히_0(ai, owner, world):
    """실접속 관찰 2차 **발견 3** 의 답 — admin 액션도 같은 표에 남는다.

    `LogEntry` 는 **그대로 0건**이다. Django 의 장부를 고친 것이 아니라
    우리 장부를 만들었다. 둘을 합치는 것은 하지 않는다.
    """
    target = order('SEED-0002')
    _verdict, outcome = services.propose_refund(ai, target, target.total_amount, '고객 요청')
    refund = outcome.refund
    assert refund.status == Refund.Status.PROPOSED

    response = login('owner', 'owner1234').post(
        '/admin/orders/refund/',
        {'action': 'approve_selected', '_selected_action': [str(refund.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    refund.refresh_from_db()
    assert refund.status == Refund.Status.APPROVED

    approved = events(Event.Transition.REFUND_APPROVED)
    cancelled = events(Event.Transition.ORDER_CANCELLED)
    assert len(approved) == 1 and len(cancelled) == 1
    assert approved[0].door == Event.Door.ADMIN == cancelled[0].door
    assert approved[0].actor == owner
    assert approved[0].call_id == cancelled[0].call_id, '한 요청이 만든 두 사실이다.'
    assert approved[0].call_id, 'admin 문에도 상관 ID 가 실린다.'
    assert cancelled[0].after == {'status': Order.Status.CANCELLED}
    assert LogEntry.objects.count() == 0, 'Django 의 장부는 여전히 0건이다(발견 3).'


@pytest.mark.django_db
def test_규칙_확정은_actor_없이_남는다(ai, world):
    """규칙이 확정하면 사람 칸은 **비어 있다.** 빠진 값이 아니라 사실이다."""
    target = order('SEED-0003')

    services.propose_refund(ai, target, 30000, '고객 요청')

    proposed = events(Event.Transition.REFUND_PROPOSED)[0]
    assert proposed.kind == Event.Kind.ALLOW
    assert proposed.rule_ids == [], 'ALLOW 에는 통과한 규칙을 싣지 않는다(4단계 결정).'

    approved = events(Event.Transition.REFUND_APPROVED)
    assert len(approved) == 1
    assert approved[0].actor is None, '규칙이 확정한 사실에는 사람이 없다.'
    assert approved[0].actor_label == ''
    assert approved[0].after['decided_via'] == Refund.Via.RULE
    assert approved[0].at >= proposed.at, '제안이 승인보다 앞 행이다.'


# --- 4. 도구면 궤적과 잇닿는가 ---------------------------------------------------


@pytest.mark.django_db
def test_도구_호출과_장부는_같은_call_id_로_잇닿는다(ai, world):
    """**시작 상태에서도 통과한다** — 호출 문맥은 패키지가 준다(django-itda v0.2).

    제안 → 판정 → 확정이 하나의 사건 사슬이 되는 지점이고, 학생이 고칠 것은
    "어디서 적는가" 이지 "어떻게 잇는가" 가 아니다.
    """
    from agent.live.tools import toolset

    target = order('SEED-0002')

    result = toolset.call(
        'propose_refund', ai, order_id=target.pk, amount=target.total_amount
    )

    call = ToolCall.objects.get(call_id=result['call_id'])
    rows = events(Event.Transition.REFUND_PROPOSED)
    assert len(rows) == 1
    assert rows[0].call_id == call.call_id == result['call_id']
    assert rows[0].door == Event.Door.MCP, '도구면으로 들어온 요청이다.'


# --- 5. 고칠 수 없는가 -----------------------------------------------------------


@pytest.fixture
def recorded(ai, world):
    """장부 한 줄. 어느 층이 막는지 보려면 먼저 하나가 있어야 한다."""
    target = order('SEED-0004')
    return Event.record(
        subject=target,
        transition=Event.Transition.ORDER_PLACED,
        kind=Event.Kind.ALLOW,
        reason='장부 시험용 한 줄.',
        actor=ai,
    )


@pytest.mark.django_db
def test_장부는_고칠_수_없다_모델_층(recorded):
    """**시작 상태에서도 통과한다** — 모델 층 거부는 준비 비용이다."""
    recorded.reason = '고쳐 쓴 사유'
    with pytest.raises(LedgerImmutable):
        recorded.save()

    with pytest.raises(LedgerImmutable):
        recorded.delete()

    with pytest.raises(LedgerImmutable):
        Event.objects.all().delete()

    assert Event.objects.get(pk=recorded.pk).reason == '장부 시험용 한 줄.'


@pytest.mark.django_db
def test_장부는_고칠_수_없다_DB_층(recorded):
    """`QuerySet.update()` 는 `save()` 를 지나지 않는다 — 모델 층은 못 막는다.

    그래서 DB 트리거가 있다. 여기서 나는 것은 `LedgerImmutable` 이 아니라
    `IntegrityError` 다 — 막은 것이 우리 코드가 아니라 **DB** 이기 때문이다.
    """
    with pytest.raises(IntegrityError) as raised, transaction.atomic():
        Event.objects.filter(pk=recorded.pk).update(reason='고쳐 쓴 사유')

    assert LEDGER_001 in str(raised.value), '무엇이 막았는지가 예외 문장에 있어야 한다.'
    assert Event.objects.get(pk=recorded.pk).reason == '장부 시험용 한 줄.'


# --- 6. 링크 발급 ---------------------------------------------------------------


@pytest.mark.django_db
def test_링크_발급은_격상일_때만_남고_actor가_있다(ai, seed4):
    """6단계가 자리만 뚫어 두었던 `actor` 인자가 여기서 쓰인다."""
    services.issue_payment_link(ai, seed4)

    rows = events(Event.Transition.PAYMENT_LINK_ISSUED)
    assert len(rows) == 1
    assert rows[0].actor == ai, '누가 링크를 발급했나 — 6단계에는 답이 없었다.'
    assert rows[0].kind == Event.Kind.ESCALATE
    assert rows[0].rule_ids == [PAY_001]
    assert rows[0].after['expires_at']

    verdict, payment = services.issue_payment_link(ai, order('SEED-0002'))

    assert verdict.kind == Verdict.DENY and payment is None
    assert len(events(Event.Transition.PAYMENT_LINK_ISSUED)) == 1, (
        '발급하지 않은 링크가 장부에 남으면 안 된다.'
    )


# --- 7. 같은 트랜잭션인가 --------------------------------------------------------


@pytest.mark.django_db
def test_같은_트랜잭션이다(ai, bob, seed4):
    """장부가 터지면 세계도 안 움직인다. 둘은 같은 트랜잭션이다.

    두 번째 기록에서 예외를 주입한다 — 앞 기록도, 상태 전이도, 재고 차감도
    함께 되돌아가야 한다. 되돌아가지 않으면 "사실은 있는데 장부엔 없는" 또는
    그 반대의 상태가 남는다.
    """
    real = Event.record
    seen = {'n': 0}

    def flaky(**kwargs):
        seen['n'] += 1
        if seen['n'] == 2:
            raise RuntimeError('장부가 터졌다')
        return real(**kwargs)

    with mock.patch.object(Event, 'record', flaky):
        with pytest.raises(RuntimeError):
            services.pay_order(bob, seed4)

    seed4.refresh_from_db()
    assert seed4.status == Order.Status.PENDING, '장부가 터졌는데 세계만 움직이면 안 된다.'
    assert limited().stock == 1
    assert Event.objects.count() == 0, '되돌아간 사실의 기록은 남지 않는다.'


# --- 8. 대장과 프롬프트 ---------------------------------------------------------


def _ledger_file():
    return (Path(__file__).resolve().parent.parent / 'RULES.md').read_text(encoding='utf-8')


def _row(ledger, rule_id):
    rows = [line for line in ledger.splitlines() if line.startswith(f'| `{rule_id}`')]
    assert len(rows) == 1, f'{rule_id} 행이 {len(rows)}개다 — 대장의 ID 는 유일해야 한다.'
    return [cell.strip() for cell in rows[0].strip('|').split('|')]


def test_규칙_대장에_LEDGER_001이_있고_앞_행은_그대로다():
    """새 법 하나는 대장에 남고, 앞 여섯 행은 ID·원문·출처까지 그대로여야 한다."""
    ledger = _ledger_file()

    cells = _row(ledger, LEDGER_001)
    assert cells[1] == LEDGER_001_TEXT, '원문은 점주가 말한 그대로여야 한다.'
    assert cells[6] == 'active'
    assert 'DELETE' in cells[7] or '삭제' in cells[7], (
        '삭제는 모델 층만 막는다 — 그 한정을 대장에 정직하게 적어야 한다.'
    )

    from orders.rules import RULE_TEXTS

    for rule_id, (text, source) in STAGE_06_ROWS.items():
        row = _row(ledger, rule_id)
        assert row[0] == f'`{rule_id}`'
        assert row[1] == text, f'{rule_id} 의 원문이 바뀌었다.'
        assert row[2] == source, f'{rule_id} 의 출처가 바뀌었다 — 배후의 사람이 사라진다.'
        assert RULE_TEXTS[rule_id] == text, '코드의 원문과 대장의 원문이 갈라졌다.'
    assert RULE_TEXTS[LEDGER_001] == LEDGER_001_TEXT


def test_프롬프트_7번은_이사됐다():
    """이사 표시는 지우기 위한 것이 아니라 **대조하기 위한** 것이다."""
    prompt = (
        Path(__file__).resolve().parent.parent / 'agent' / 'prompts' / 'ai_staff.md'
    ).read_text(encoding='utf-8')
    lines = prompt.splitlines()

    seventh = next(index for index, line in enumerate(lines) if line.startswith('7. '))

    assert LEDGER_001_TEXT in lines[seventh], '원문은 지우지 않는다.'
    assert f'코드로 이사됨 (`{LEDGER_001}' in lines[seventh + 1]
    assert 'ledger/models.py:Event.record' in lines[seventh + 1]
