"""주문 모델."""

import uuid
from datetime import date, timedelta

from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from shop.models import Product

from .rules import ESCALATE_OVER, ORDER_001, REFUND_001, REFUND_002, REFUND_WINDOW_DAYS, RULE_TEXTS
from .verdict import Outcome, Verdict


class InsufficientStock(Exception):
    """주문 상품의 재고가 부족해 결제를 완료할 수 없을 때 발생."""


class InvalidTransition(Exception):
    """지금 상태에서는 갈 수 없는 곳으로 가려 했을 때 발생 — DENY 판정을 들고 다닌다."""

    def __init__(self, verdict):
        self.verdict = verdict
        super().__init__(verdict.reason)


class Order(models.Model):
    """주문. 결제 대기 상태로 생성되고 결제 성공 시 '결제 완료'가 된다."""

    class Status(models.TextChoices):
        PENDING = 'pending', '결제 대기'
        PAID = 'paid', '결제 완료'
        SHIPPING = 'shipping', '배송 중'
        COMPLETED = 'completed', '배송 완료'
        CANCELLED = 'cancelled', '취소'

    order_number = models.CharField('주문번호', max_length=30, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='주문자',
        on_delete=models.CASCADE,
        related_name='orders',
    )
    status = models.CharField('상태', max_length=20, choices=Status.choices, default=Status.PENDING)
    recipient_name = models.CharField('받는 사람', max_length=50)
    phone = models.CharField('연락처', max_length=20)
    address = models.CharField('주소', max_length=200)
    total_amount = models.PositiveIntegerField('총 결제 금액')
    created_at = models.DateTimeField('주문일', auto_now_add=True)
    updated_at = models.DateTimeField('수정일', auto_now=True)

    class Meta:
        verbose_name = '주문'
        verbose_name_plural = '주문'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.order_number} ({self.user})'

    def save(self, *args, **kwargs):
        if not self.order_number:
            today = date.today().strftime('%Y%m%d')
            self.order_number = f'{today}-{uuid.uuid4().hex[:8].upper()}'
        super().save(*args, **kwargs)

    def mark_paid(self):
        """전이 계약: `pending` 에서만 `paid` 로 간다 (ORDER-001@v1).

        `WHERE status = 'pending'` 을 붙인 UPDATE 를 먼저 쏘고 rowcount 를 본다.
        0 이 뜻하는 것은 정확히 하나다 — **대상 PK 이면서 pending 인 행이 없다**.
        다른 요청이 먼저 지나갔을 수도 있고, 애초에 pending 이 아니었을 수도 있다.
        rowcount 0 자체는 경합의 증거가 아니라 조건 불일치의 증거다.

        전이를 **재고 차감보다 먼저** 판정한다. 롤백 때문이 아니라(둘 다 같은
        트랜잭션 안이라 어느 순서든 예외가 나면 함께 되돌아간다) 어차피 거부할
        요청에 불필요한 재고 갱신을 하지 않기 위해서다.

        재고 차감은 `stock >= 수량` 조건부 UPDATE로 수행한다. 결제 직전에 재고가
        줄어든 경쟁 상황에서도 초과 판매가 일어나지 않으며, 재고가 부족하면
        InsufficientStock을 일으키고 상태 전이와 앞 상품 차감까지 통째로 되돌린다.
        """
        with transaction.atomic():
            moved = Order.objects.filter(pk=self.pk, status=self.Status.PENDING).update(
                status=self.Status.PAID
            )
            if not moved:
                raise InvalidTransition(
                    Verdict(
                        kind=Verdict.DENY,
                        rule_ids=[ORDER_001],
                        # 여기서 `self.status` 를 찍지 않는다 — 재조회하지 않은
                        # 메모리 위의 값을 DB 의 현재 상태인 양 말할 수 없다.
                        reason=(
                            f'{ORDER_001}: 결제 대기 상태가 아니다(조건 불일치). '
                            f'{RULE_TEXTS[ORDER_001]}'
                        ),
                        alternatives=['주문 내역에서 이 주문의 현재 상태를 조회한다'],
                    )
                )
            for item in self.items.select_related('product'):
                if item.product is None:
                    raise InsufficientStock(
                        f'{item.product_name} 상품이 삭제되어 결제할 수 없습니다.'
                    )
                updated = Product.objects.filter(
                    pk=item.product.pk, stock__gte=item.quantity
                ).update(stock=F('stock') - item.quantity)
                if not updated:
                    raise InsufficientStock(
                        f'{item.product_name}의 재고가 부족합니다. '
                        f'(남은 재고 {item.product.stock}개)'
                    )
            # 상태는 위에서 이미 옮겼다. 여기서는 메모리 위의 객체만 맞춰 준다.
            self.status = self.Status.PAID
            self.save(update_fields=['updated_at'])


class OrderItem(models.Model):
    """주문 상품. 결제 시점의 상품명·가격을 스냅샷으로 보관한다."""

    order = models.ForeignKey(
        Order, verbose_name='주문', on_delete=models.CASCADE, related_name='items'
    )
    product = models.ForeignKey(
        'shop.Product',
        verbose_name='상품',
        on_delete=models.SET_NULL,
        null=True,
        related_name='order_items',
    )
    product_name = models.CharField('상품명', max_length=100)
    unit_price = models.PositiveIntegerField('단가')
    quantity = models.PositiveIntegerField('수량')

    class Meta:
        verbose_name = '주문 상품'
        verbose_name_plural = '주문 상품'

    def __str__(self):
        return f'{self.product_name} x{self.quantity}'

    @property
    def total_price(self):
        return self.unit_price * self.quantity


class Refund(models.Model):
    """환불. AI 직원이 *제안*하고 점주가 *확정*한다.

    태어날 때는 '제안됨'이다. 제안만으로는 주문이 움직이지 않는다.
    """

    class Status(models.TextChoices):
        PROPOSED = 'proposed', '제안됨'
        APPROVED = 'approved', '승인'
        REJECTED = 'rejected', '거부'

    class Via(models.TextChoices):
        """무엇이 확정했는가 — 사람인가 규칙인가."""

        OWNER = 'owner', '사람(점주)'
        RULE = 'rule', '규칙'

    order = models.ForeignKey(
        Order, verbose_name='주문', on_delete=models.CASCADE, related_name='refunds'
    )
    amount = models.PositiveIntegerField('환불 금액')
    reason = models.CharField('사유', max_length=200)
    status = models.CharField(
        '상태', max_length=20, choices=Status.choices, default=Status.PROPOSED
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='요청자',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requested_refunds',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='결정자',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='decided_refunds',
    )
    decided_via = models.CharField(
        '확정 주체', max_length=20, choices=Via.choices, blank=True, default=''
    )
    created_at = models.DateTimeField('생성일', auto_now_add=True)
    decided_at = models.DateTimeField('결정일', null=True, blank=True)

    class Meta:
        verbose_name = '환불'
        verbose_name_plural = '환불'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.order.order_number} 환불 {self.amount:,}원'

    @classmethod
    def decide(cls, order, amount, requested_by=None):
        """환불 요청을 판정한다 — 세계의 대답(Verdict)만 만들고, 아무것도 쓰지 않는다.

        순서가 곧 법의 우선순위다. **기한을 먼저 본다** — 7일이 지난 건은
        금액이 얼마든 격상 대상이 아니라 거부 대상이다.

        기한 판정에는 두 가지 약속이 붙는다.

        - **판정 기준 시점은 신청 시점**이다. 고액 건이 승인 큐에서 기다리다가
          기한을 넘겨도 승인 시점에 다시 검사하지 않는다(그 재검사는 5단계 소재).
        - **결제일은 `created_at`(주문 생성 시각)으로 본다.** 결제 시각을 따로
          들고 있지 않아서 두는 교육상 가정이다(`paid_at` 은 7단계 장부에서 생긴다).

        경과 시간은 `.days` 로 자르지 않고 정확한 시간 차로 비교한다. 자르면
        7일 23시간짜리 요청이 "7일"이 되어 통과한다 — 원문은 "7일이 지난"이므로
        정확히 7일까지는 허용하고 7일을 **초과**한 건만 거부한다.
        """
        elapsed = timezone.now() - order.created_at
        if elapsed > timedelta(days=REFUND_WINDOW_DAYS):
            return Verdict(
                kind=Verdict.DENY,
                rule_ids=[REFUND_001],
                reason=(
                    f'{REFUND_001}: 결제 후 {REFUND_WINDOW_DAYS}일이 지났다 '
                    f'({elapsed.days}일 {elapsed.seconds // 3600}시간 경과). '
                    f'{RULE_TEXTS[REFUND_001]}'
                ),
                # 없는 길을 적지 않는다. 예외 승인 큐도, 교환·재발송 경로도
                # 이 세계에는 아직 없다 — 지금 실제로 할 수 있는 것은 조회뿐이다.
                alternatives=['주문 내역에서 환불 가능 기한을 확인한다'],
            )
        if amount > ESCALATE_OVER:
            return Verdict(
                kind=Verdict.ESCALATE,
                rule_ids=[REFUND_002],
                reason=(
                    f'{REFUND_002}: 환불 {amount:,}원은 {ESCALATE_OVER:,}원을 넘는다. '
                    f'{RULE_TEXTS[REFUND_002]}'
                ),
                # "임계 아래로 쪼개서 다시 올려라"는 대안이 아니라 승인 회피다.
                alternatives=['점주 승인 큐에서 확정을 기다린다'],
            )
        return Verdict(
            kind=Verdict.ALLOW,
            rule_ids=[],
            reason=(
                f'기한 안({elapsed.days}일 경과)이고 {ESCALATE_OVER:,}원 이하다 '
                f'— 점주가 승인한 정책 범위라 규칙이 확정한다.'
            ),
            alternatives=[],
        )

    @classmethod
    def apply(cls, order, amount, reason, requested_by):
        """판정하고, 판정대로 세계를 움직인다. `(Verdict, Outcome)` 을 돌려준다.

        **판정과 결과는 다른 값이다.** ALLOW 는 "허용된다"까지고, 세계가 실제로
        움직였는지는 Outcome 이 말한다.

        DENY 면 아무것도 만들지 않는다 — 거부된 요청은 승인 큐를 더럽히지 않는다.
        ALLOW 면 규칙이 확정한다(`decided_via='rule'`). 사람이 확정한 것과는
        다른 사건이므로 구분해 남긴다(7단계 장부의 소재).

        제안 생성부터 승인·주문 취소까지가 **하나의 트랜잭션**이다. 중간에
        실패하면 승인된 환불만 남고 주문은 안 취소된 상태가 생기는데, 그건
        "판정은 있었는데 세계는 반쪽만 움직인" 상태라 있어서는 안 된다.

        막지 **않는** 것도 분명히 해 둔다 — 같은 주문에 두 번 신청하면 승인된
        환불이 두 개 생긴다. 중복·경합은 5단계의 소재다.
        """
        verdict = cls.decide(order, amount, requested_by)
        if verdict.kind == Verdict.DENY:
            return verdict, Outcome(state=Outcome.NOTHING)

        with transaction.atomic():
            refund = cls.objects.create(
                order=order, amount=amount, reason=reason, requested_by=requested_by
            )
            if verdict.kind == Verdict.ALLOW:
                refund.approve(by=None, via=cls.Via.RULE)
                return verdict, Outcome(state=Outcome.COMMITTED, refund=refund)
            return verdict, Outcome(state=Outcome.QUEUED, refund=refund)

    def current_outcome(self):
        """이미 있는 제안의 **지금 처리 상태**를 판정 형식으로 돌려준다.

        다시 `decide()` 를 부르지 않는다. 사람이 이미 승인한 건에 대고 규칙이
        "격상 대상입니다"라고 답하면, 그건 판정이 아니라 현실을 못 본 것이다.
        """
        if self.status == self.Status.APPROVED:
            kind = Verdict.ALLOW
            note = f'이미 승인되어 주문이 {self.order.get_status_display()} 입니다.'
        elif self.status == self.Status.REJECTED:
            kind = Verdict.DENY
            note = '이미 점주가 거부한 환불입니다.'
        else:
            kind = Verdict.ESCALATE
            note = '이미 승인 큐에 올라가 있습니다 — 점주의 확정을 기다립니다.'
        return (
            Verdict(
                kind=kind,
                rule_ids=[],
                reason=f'{self.order.order_number} 환불 {self.amount:,}원 — {note}',
                alternatives=[],
            ),
            Outcome(state=Outcome.ALREADY, refund=self),
        )

    def approve(self, by, via=None):
        """확정한다 — 환불을 승인하고 주문을 취소로 옮긴다.

        누가 확정했는지(`decided_by`)와 **무엇이** 확정했는지(`decided_via`)는 다르다.
        규칙이 확정하면 사람 칸은 비어 있고, 그래도 확정한 주체는 있다.

        두 저장은 **하나의 트랜잭션**이다. 주문 저장이 실패했는데 환불만
        approved 로 남으면, 장부는 "환불했다"는데 주문은 살아 있게 된다.
        admin·shell 에서 직접 불러도 같은 보장이 걸리도록 메서드 안에 둔다.
        """
        with transaction.atomic():
            self.status = self.Status.APPROVED
            self.decided_by = by
            self.decided_via = via or (self.Via.OWNER if by else self.Via.RULE)
            self.decided_at = timezone.now()
            self.save(update_fields=['status', 'decided_by', 'decided_via', 'decided_at'])

            self.order.status = Order.Status.CANCELLED
            self.order.save(update_fields=['status', 'updated_at'])

    def reject(self, by, note=''):
        """점주가 거부한다 — 주문은 그대로 두고, 거부했다는 사실을 남긴다."""
        self.status = self.Status.REJECTED
        self.decided_by = by
        self.decided_via = self.Via.OWNER if by else self.Via.RULE
        self.decided_at = timezone.now()
        if note:
            self.reason = f'{self.reason} / 점주 메모: {note}'[:200]
        self.save(
            update_fields=['status', 'decided_by', 'decided_via', 'decided_at', 'reason']
        )
