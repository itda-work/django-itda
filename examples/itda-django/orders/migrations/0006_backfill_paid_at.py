"""backfill — 이미 있는 행을 법에 맞춘다 (7단계).

선언 한 줄(`CheckConstraint`)의 진짜 비용은 **이미 있는 데이터**다. 다음
마이그레이션(contract)을 이것 없이 적용하면 시드 주문에서 `IntegrityError` 가
난다. 그 실패가 이 단계의 '원가계산' 이다.

**취소 주문은 건드리지 않는다.** 결제 뒤 취소인지 결제 전 취소인지, 장부가
없던 시절의 행에 대해서는 **알 수 없다.** 모름을 모름으로 둔다 — 여기서
`created_at` 을 채워 넣으면 그 순간 장부가 아니라 추측이 데이터가 된다.

결제 시각을 `created_at` 으로 보는 것은 4단계 `Refund.decide` 가 주석으로
적어 둔 교육상 가정이다. 그 가정이 여기서 **데이터**가 된다.
"""

from django.db import migrations

# 결제가 끝난 뒤에만 갈 수 있는 상태들. 이 행들은 결제 시각을 가져야 한다.
AFTER_PAYMENT = ('paid', 'shipping', 'completed')


def fill(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    for order in Order.objects.filter(status__in=AFTER_PAYMENT, paid_at__isnull=True):
        Order.objects.filter(pk=order.pk).update(paid_at=order.created_at)


def unfill(apps, schema_editor):
    """되돌리면 결제 시각을 비운다 — 확정 정보가 아니라 채워 넣은 값이기 때문이다."""
    Order = apps.get_model('orders', 'Order')
    Order.objects.filter(status__in=AFTER_PAYMENT).update(paid_at=None)


class Migration(migrations.Migration):
    dependencies = [('orders', '0005_order_paid_at')]

    operations = [migrations.RunPython(fill, unfill)]
