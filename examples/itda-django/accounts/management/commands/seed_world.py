"""세계를 만든다 — 계정·카테고리·상품·주문을 심는 시드 명령.

몇 번을 실행해도 같은 세계가 되도록 짰다(멱등). 주문번호·username·slug 같은
고정 열쇠로 찾아 없으면 만들고, 있으면 값만 맞춘다.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem
from shop.models import Category, Product

User = get_user_model()

# (username, 비밀번호, is_staff, is_superuser, 그룹, 설명)
#
# 점주는 superuser 가 아니다. 승인할 수 있는 이유가 '전능해서'가 아니라
# '그 권한을 가져서'여야 권한이 법이라는 것이 보인다. 전능한 계정은 admin 하나로 뺐다.
ACCOUNTS = [
    ('admin', 'admin1234', True, True, None, '슈퍼유저(교육용 뒷문)'),
    ('owner', 'owner1234', True, False, '점주', '점주'),
    ('ai-staff', 'ai1234', False, False, 'AI직원', 'AI 직원 서비스 계정'),
    ('alice', 'pass1234', False, False, None, '고객'),
    ('bob', 'pass1234', False, False, None, '고객'),
]

# 그룹 = 자리. 그 자리에서 할 수 있는 일의 목록이 곧 권한이다.
GROUPS = {
    # AI 직원은 '제안'까지다. 환불을 만들 수는 있어도 바꿀 수는 없다.
    #
    # change_order 는 있다 — 주문 접수와 결제 처리는 AI 직원의 업무이기 때문이다.
    # 권한은 '누가 손댈 수 있는가'만 답한다. '어떤 상태에서 어디로 갈 수 있는가'는
    # 다른 법의 몫이다(4단계 전이 계약).
    #
    # view_refund 는 열람이지 확정이 아니다 — 자기가 올린 제안이 승인됐는지
    # 물어볼 수 있어야 실접속 트랙의 폴링(`GET /api/refunds/<id>/`)이 성립한다.
    'AI직원': [
        'orders.add_order',
        'orders.view_order',
        'orders.change_order',
        'orders.add_refund',
        'orders.view_refund',
        'shop.view_product',
    ],
    # 점주는 '확정'한다. change_refund 한 줄이 승인 큐의 액션을 켜는 열쇠다.
    '점주': [
        'orders.view_order',
        'orders.change_order',
        'orders.view_orderitem',
        'orders.view_refund',
        'orders.change_refund',
        'shop.view_category',
        'shop.view_product',
        'accounts.view_user',
    ],
}

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
    # 재고를 넉넉히 둔다 — 4단계 ⑥ 카드에서 재고가 두 번 깎여도 '소진'이 아니라
    # '상태를 안 봤다'가 원인임이 드러나야 하기 때문이다.
    ('stationery', '만년필 잉크 30ml', '🖋️', 18000, 30, '물에 강한 안료 잉크.'),
    ('stationery', '스탬프 세트 12종', '🧷', 22000, 20, '손편지용 고무 스탬프 열두 개.'),
]

# (주문번호, 주문자, 상태, 며칠 전, 받는 사람, 연락처, 주소, [(상품명, 수량)])
#
# 금액은 5만원 임계(4단계 REFUND-002)를 사이에 두고 갈라 둔다.
#   SEED-0001  22,500원 · 8일 전  — 임계 아래지만 7일이 지났다
#   SEED-0002  54,000원 · 3일 전  — 임계 위 → 점주에게 올라간다
#   SEED-0003  68,000원 · 1일 전  — 임계 위 → 점주에게 올라간다
ORDERS = [
    (
        'SEED-0001', 'alice', Order.Status.COMPLETED, 8,
        '김앨리스', '010-0000-0001', '서울시 가상구 없는동 1-1',
        [('수제 초코쿠키 6개입', 2), ('무선 노트 A5', 1)],
    ),
    (
        'SEED-0002', 'alice', Order.Status.PAID, 3,
        '김앨리스', '010-0000-0001', '서울시 가상구 없는동 1-1',
        [('만년필 잉크 30ml', 3)],
    ),
    (
        'SEED-0003', 'bob', Order.Status.PAID, 1,
        '박밥', '010-0000-0002', '부산시 가상구 없는동 2-2',
        [('스탬프 세트 12종', 2), ('유자 캐러멜 한 상자', 2)],
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
        groups = self._seed_groups()
        users = self._seed_users(groups)
        categories = self._seed_categories()
        products = self._seed_products(categories)
        self._seed_orders(users, products)

        self.stdout.write(
            self.style.SUCCESS(
                f'세계 준비 완료 — 사용자 {User.objects.count()}명, '
                f'상품 {Product.objects.count()}개, 주문 {Order.objects.count()}건'
            )
        )

    def _seed_groups(self):
        groups = {}
        for name, codenames in GROUPS.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(self._permissions(codenames))
            groups[name] = group
            self.stdout.write(f'  그룹 {name} — 권한 {len(codenames)}개')
        return groups

    def _permissions(self, codenames):
        permissions = []
        for codename in codenames:
            app_label, code = codename.split('.')
            permissions.append(
                Permission.objects.get(content_type__app_label=app_label, codename=code)
            )
        return permissions

    def _seed_users(self, groups):
        users = {}
        for username, password, is_staff, is_superuser, group_name, note in ACCOUNTS:
            user, created = User.objects.get_or_create(username=username)
            user.is_staff = is_staff
            user.is_superuser = is_superuser
            user.set_password(password)
            user.save()
            user.groups.set([groups[group_name]] if group_name else [])
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
