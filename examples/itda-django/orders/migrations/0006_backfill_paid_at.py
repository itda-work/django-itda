"""backfill — 이미 있는 행을 법에 맞춘다 (7단계).

선언 한 줄(`CheckConstraint`)의 진짜 비용은 **이미 있는 데이터**다. 다음
마이그레이션(contract)을 이것 없이 적용하면 시드 주문에서 `IntegrityError` 가
난다. 그 실패가 이 단계의 '원가계산' 이다.

**취소 주문은 건드리지 않는다.** 결제 뒤 취소인지 결제 전 취소인지, 지금
DB 에 남은 것만으로는 **일괄 복원할 근거가 없다.** `Refund.decided_at` 은
환불 결정 시각이지 결제 시각이 아니고, 현재 환불 판정은 결제 대기 주문도
허용한다. 모름을 모름으로 둔다 — 여기서 `created_at` 을 채워 넣으면 그 순간
장부가 아니라 추측이 데이터가 된다. (DB 밖의 근거 — 과거 실접속 기록 같은 것 —
으로 개별 주문의 결제를 아는 것은 별개다. 여기서 하지 않을 뿐이다.)

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


class Migration(migrations.Migration):
    dependencies = [('orders', '0005_order_paid_at')]

    # 역방향은 **아무것도 하지 않는다.** 되돌리며 지우는 backfill 은 자기가
    # 채운 행과 그 뒤에 실제로 결제된 행을 구별하지 못해서, 관측된 결제 시각까지
    # NULL 로 만든다(sol 리뷰 발견 4, 실측). 데이터 마이그레이션은 앞으로만
    # 간다 — expand 를 되돌리면 열 자체가 사라지는 것은 스키마의 일이고 별개다.
    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
