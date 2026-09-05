"""주문 모델."""

import uuid
from datetime import date

from django.conf import settings
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from shop.models import Product


class InsufficientStock(Exception):
    """주문 상품의 재고가 부족해 결제를 완료할 수 없을 때 발생."""


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
        """재고를 차감하고 주문을 결제 완료 상태로 바꾼다.

        재고 차감은 `stock >= 수량` 조건부 UPDATE로 수행한다. 결제 직전에 재고가
        줄어든 경쟁 상황에서도 초과 판매가 일어나지 않으며, 재고가 부족하면
        InsufficientStock을 일으키고 지금까지의 차감을 모두 되돌린다.
        """
        with transaction.atomic():
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
            self.status = self.Status.PAID
            self.save(update_fields=['status', 'updated_at'])


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
    created_at = models.DateTimeField('생성일', auto_now_add=True)
    decided_at = models.DateTimeField('결정일', null=True, blank=True)

    class Meta:
        verbose_name = '환불'
        verbose_name_plural = '환불'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.order.order_number} 환불 {self.amount:,}원'

    def approve(self, by):
        """점주가 확정한다 — 환불을 승인하고 주문을 취소로 옮긴다.

        지금은 '누가 확정했는가'만 기록한다. 이미 승인된 제안을 또 승인하면 어떻게
        되는지, 그 사이에 주문 상태가 바뀌었으면 어떻게 되는지는 아직 아무도 안 본다
        (전이 계약은 4단계에서 이사 온다).
        """
        self.status = self.Status.APPROVED
        self.decided_by = by
        self.decided_at = timezone.now()
        self.save(update_fields=['status', 'decided_by', 'decided_at'])

        self.order.status = Order.Status.CANCELLED
        self.order.save(update_fields=['status', 'updated_at'])

    def reject(self, by, note=''):
        """점주가 거부한다 — 주문은 그대로 두고, 거부했다는 사실을 남긴다."""
        self.status = self.Status.REJECTED
        self.decided_by = by
        self.decided_at = timezone.now()
        if note:
            self.reason = f'{self.reason} / 점주 메모: {note}'[:200]
        self.save(update_fields=['status', 'decided_by', 'decided_at', 'reason'])
