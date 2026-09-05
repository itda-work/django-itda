"""주문 모델."""

import uuid
from datetime import date

from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from shop.models import Product

from .rules import ESCALATE_OVER, ORDER_001, REFUND_001, REFUND_002, REFUND_WINDOW_DAYS, RULE_TEXTS
from .verdict import Verdict


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
        0이면 누군가 이미 지나갔다는 뜻이므로 **재고를 건드리기 전에** 멈춘다.
        (이 순서가 뒤집히면 거부당한 요청이 재고를 이미 깎아 놓는다.)

        재고 차감은 `stock >= 수량` 조건부 UPDATE로 수행한다. 결제 직전에 재고가
        줄어든 경쟁 상황에서도 초과 판매가 일어나지 않으며, 재고가 부족하면
        InsufficientStock을 일으키고 상태 전이까지 통째로 되돌린다.
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
                        reason=(
                            f'{ORDER_001}: 결제 대기 상태가 아니다 '
                            f'(현재 {self.get_status_display()}).'
                        ),
                        alternatives=[
                            '이미 결제된 주문이다 — 결제 내역을 조회해 확인한다',
                            '취소된 주문이라면 새 주문을 접수한다',
                        ],
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

        결제 시각을 따로 들고 있지 않아 지금은 주문 생성 시각을 결제일로 본다
        (`paid_at` 은 7단계 장부에서 생긴다).
        """
        days = (timezone.now() - order.created_at).days
        if days > REFUND_WINDOW_DAYS:
            return Verdict(
                kind=Verdict.DENY,
                rule_ids=[REFUND_001],
                reason=(
                    f'{REFUND_001}: 결제 후 {REFUND_WINDOW_DAYS}일이 지났다 '
                    f'({days}일 경과). {RULE_TEXTS[REFUND_001]}'
                ),
                alternatives=['점주에게 예외 승인을 요청(격상)', '부분 교환·재발송으로 전환'],
            )
        if amount > ESCALATE_OVER:
            return Verdict(
                kind=Verdict.ESCALATE,
                rule_ids=[REFUND_002],
                reason=(
                    f'{REFUND_002}: 환불 {amount:,}원은 {ESCALATE_OVER:,}원을 넘는다. '
                    f'{RULE_TEXTS[REFUND_002]}'
                ),
                alternatives=[
                    f'{ESCALATE_OVER:,}원 이하로 분할 환불',
                    '점주 승인 큐에서 확정을 기다린다',
                ],
            )
        return Verdict(
            kind=Verdict.ALLOW,
            rule_ids=[],
            reason=f'기한 안({days}일)이고 {ESCALATE_OVER:,}원 이하다 — 규칙이 확정한다.',
            alternatives=[],
        )

    @classmethod
    def apply(cls, order, amount, reason, requested_by):
        """판정하고, 판정대로 세계를 움직인다.

        DENY 면 아무것도 만들지 않는다 — 거부된 요청은 승인 큐를 더럽히지 않는다.
        ALLOW 면 규칙이 확정한다(`decided_via='rule'`). 사람이 확정한 것과는
        다른 사건이므로 구분해 남긴다(7단계 장부의 소재).
        """
        verdict = cls.decide(order, amount, requested_by)
        if verdict.kind == Verdict.DENY:
            return verdict, None

        refund = cls.objects.create(
            order=order, amount=amount, reason=reason, requested_by=requested_by
        )
        if verdict.kind == Verdict.ALLOW:
            refund.approve(by=None, via=cls.Via.RULE)
        return verdict, refund

    def approve(self, by, via=None):
        """확정한다 — 환불을 승인하고 주문을 취소로 옮긴다.

        누가 확정했는지(`decided_by`)와 **무엇이** 확정했는지(`decided_via`)는 다르다.
        규칙이 확정하면 사람 칸은 비어 있고, 그래도 확정한 주체는 있다.
        """
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
