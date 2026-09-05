"""3단계 — 워밍업: 보장이 *존재*한다는 것의 실측.

재고를 음수로 만들려고 세 경로로 두드린다. 갈수록 Django 에서 멀어지는 순서다.
세 번 다 `IntegrityError` 로 막힌다 — 막은 것은 우리 Python 코드가 아니라
DB 테이블 정의에 새겨진 `CHECK ("stock" >= 0)` 이고, 그 출처는
`shop/models.py` 의 `PositiveIntegerField` 단어 하나다.

여기서 `IntegrityError` 는 실패가 아니라 **보장**이다.

제약 위반은 트랜잭션을 깨뜨리므로 시도마다 `transaction.atomic()` 으로 감싼다.
감싸지 않으면 뒤따르는 쿼리가 전부 TransactionManagementError 로 죽는다.
"""

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from shop.models import Product

PRODUCT_NAME = '수제 초코쿠키 6개입'


@pytest.fixture
def product(world):
    return Product.objects.get(name=PRODUCT_NAME)


@pytest.mark.django_db
def test_경로1_ORM_정규_경로는_막힌다(product):
    """가장 평범한 방법 — 필드에 대입하고 save()."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            product.stock = -5
            product.save()

    assert Product.objects.get(pk=product.pk).stock >= 0


@pytest.mark.django_db
def test_경로2_save와_clean을_건너뛰어도_막힌다(product):
    """update() 는 Python 객체를 거치지 않고 UPDATE 문 하나를 바로 쏜다.

    save() 에 넣어 둔 검증이 있었더라도 이 경로에는 소용없다. 그런데도 막힌다.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Product.objects.filter(pk=product.pk).update(stock=F('stock') - 999)

    assert Product.objects.get(pk=product.pk).stock == product.stock


@pytest.mark.django_db
def test_경로3_Django를_완전히_우회해도_막힌다(product):
    """ORM 을 통째로 건너뛴 raw SQL. DB 가 직접 거부한다.

    학생은 이 경로를 `sqlite3 db.sqlite3 "UPDATE ..."` 로 실습한다 —
    Django 가 실행되고 있지도 않은데 거부당하는 것이 요점이다.
    """
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    'UPDATE shop_product SET stock = -1 WHERE id = %s', [product.pk]
                )

    assert Product.objects.get(pk=product.pk).stock >= 0


@pytest.mark.django_db
def test_법의_원문은_테이블_정의_안에_있다(product):
    """`PositiveIntegerField` 한 단어가 마이그레이션을 타고 DB 에 새겨졌다."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT sql FROM sqlite_master WHERE name = 'shop_product'")
        create_sql = cursor.fetchone()[0].lower()

    assert 'check ("stock" >= 0)' in create_sql


@pytest.mark.django_db
def test_같은_법이_정상_범위는_그대로_통과시킨다(product):
    """제약은 모든 쓰기를 막는 것이 아니다. 약속을 어기는 쓰기만 막는다."""
    before = product.stock

    Product.objects.filter(pk=product.pk).update(stock=F('stock') - 1)

    assert Product.objects.get(pk=product.pk).stock == before - 1
