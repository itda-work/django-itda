"""세계를 만든다 — 계정·카테고리·상품·주문을 심는 시드 명령.

몇 번을 실행해도 같은 세계가 되도록 짰다(멱등). 주문번호·username·slug 같은
고정 열쇠로 찾아 없으면 만들고, 있으면 값만 맞춘다.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem
from shop.models import Category, Product

User = get_user_model()

# (username, 비밀번호, is_staff, is_superuser, 설명)
ACCOUNTS = [
    ('owner', 'owner1234', True, True, '점주'),
    ('ai-staff', 'ai1234', False, False, 'AI 직원 서비스 계정'),
    ('alice', 'pass1234', False, False, '고객'),
    ('bob', 'pass1234', False, False, '고객'),
]

# (슬러그, 이름)
CATEGORIES = [
    ('snack', '간식'),
    ('stationery', '문구'),
]

# (카테고리 슬러그, 상품명, 이모지, 가격, 재고, 설명)
PRODUCTS = [
    ('snack', '수제 초코쿠키 6개입', '🍪', 8000, 40, '버터를 넉넉히 넣어 구운 진한 초코쿠키.'),
    ('snack', '유자 캐러멜 한 상자', '🍬', 12000, 25, '유자청을 졸여 만든 새콤달콤 캐러멜.'),
    ('snack', '한정판 흑임자 다쿠아즈', '🥮', 15000, 1, '주 1회만 굽는 한정 수량 디저트.'),
    ('stationery', '무선 노트 A5', '📓', 6500, 60, '펼쳐도 눕는 무선 제본 노트.'),
    ('stationery', '만년필 잉크 30ml', '🖋️', 18000, 18, '물에 강한 안료 잉크.'),
    ('stationery', '스탬프 세트 12종', '🧷', 22000, 12, '손편지용 고무 스탬프 열두 개.'),
]

# (주문번호, 주문자, 상태, 며칠 전, 받는 사람, 연락처, 주소, [(상품명, 수량)])
ORDERS = [
    (
        'SEED-0001', 'alice', Order.Status.COMPLETED, 8,
        '김앨리스', '010-0000-0001', '서울시 가상구 없는동 1-1',
        [('수제 초코쿠키 6개입', 2), ('무선 노트 A5', 1)],
    ),
    (
        'SEED-0002', 'alice', Order.Status.PAID, 3,
        '김앨리스', '010-0000-0001', '서울시 가상구 없는동 1-1',
        [('만년필 잉크 30ml', 1)],
    ),
    (
        'SEED-0003', 'bob', Order.Status.PAID, 1,
        '박밥', '010-0000-0002', '부산시 가상구 없는동 2-2',
        [('스탬프 세트 12종', 1), ('유자 캐러멜 한 상자', 2)],
    ),
    (
        'SEED-0004', 'bob', Order.Status.PENDING, 0,
        '박밥', '010-0000-0002', '부산시 가상구 없는동 2-2',
        [('한정판 흑임자 다쿠아즈', 1)],
    ),
]


class Command(BaseCommand):
    help = '교육용 세계(계정·상품·주문)를 심는다. 여러 번 실행해도 안전하다.'

    @transaction.atomic
    def handle(self, *args, **options):
        users = self._seed_users()
        categories = self._seed_categories()
        products = self._seed_products(categories)
        self._seed_orders(users, products)

        self.stdout.write(
            self.style.SUCCESS(
                f'세계 준비 완료 — 사용자 {User.objects.count()}명, '
                f'상품 {Product.objects.count()}개, 주문 {Order.objects.count()}건'
            )
        )

    def _seed_users(self):
        users = {}
        for username, password, is_staff, is_superuser, note in ACCOUNTS:
            user, created = User.objects.get_or_create(username=username)
            user.is_staff = is_staff
            user.is_superuser = is_superuser
            user.set_password(password)
            user.save()
            users[username] = user
            self.stdout.write(f'  사용자 {username} ({note}) {"생성" if created else "갱신"}')
        return users

    def _seed_categories(self):
        categories = {}
        for slug, name in CATEGORIES:
            category, _ = Category.objects.update_or_create(slug=slug, defaults={'name': name})
            categories[slug] = category
        return categories

    def _seed_products(self, categories):
        products = {}
        for slug, name, emoji, price, stock, description in PRODUCTS:
            product, _ = Product.objects.update_or_create(
                name=name,
                defaults={
                    'category': categories[slug],
                    'emoji': emoji,
                    'price': price,
                    'stock': stock,
                    'description': description,
                },
            )
            products[name] = product
        return products

    def _seed_orders(self, users, products):
        now = timezone.now()
        for number, username, status, days_ago, recipient, phone, address, lines in ORDERS:
            items = [(products[name], quantity) for name, quantity in lines]
            total = sum(product.price * quantity for product, quantity in items)

            order, created = Order.objects.get_or_create(
                order_number=number,
                defaults={
                    'user': users[username],
                    'status': status,
                    'recipient_name': recipient,
                    'phone': phone,
                    'address': address,
                    'total_amount': total,
                },
            )
            if not created:
                Order.objects.filter(pk=order.pk).update(
                    user=users[username],
                    status=status,
                    recipient_name=recipient,
                    phone=phone,
                    address=address,
                    total_amount=total,
                )

            # auto_now_add 는 대입으로 바꿀 수 없어 UPDATE 로 주문일을 과거로 밀어 둔다.
            Order.objects.filter(pk=order.pk).update(created_at=now - timedelta(days=days_ago))

            order.items.all().delete()
            for product, quantity in items:
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product.name,
                    unit_price=product.price,
                    quantity=quantity,
                )
