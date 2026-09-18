"""2단계 — 귀속: 법이 코드가 아니라 데이터로 존재한다.

1단계에서 403을 만든 것은 우리가 쓴 코드가 아니다. `permission_required` 한 줄은
*물어봤을* 뿐이고, 답은 `auth_permission`·`auth_group` 테이블의 행이 했다.
여기서는 그 행들과, 정체성이 서명 세션에서 온다는 것과, Django 가 공짜로 주는
장부(`LogEntry`)가 어디까지만 적는지를 단언한다.

이 단계는 코드를 고치지 않는다. 그래서 이 테스트는 1단계 완료 상태에서 이미 통과한다.
새 법을 켠 것이 아니라, 이미 켜져 있던 법을 붙잡아 둔 것이다.

## 사후 변경(2026-09-19, PG 프로브)

`test_도메인_법은_아직_어디에도_시행되지_않는다` 는 `sqlite_master` 에서 테이블 정의 원문을
읽는다 — SQLite 의 성질이다. 다른 백엔드에서는 `skipif` 로 건너뛴다. 본문·단언은 그대로다.
"""

import pytest
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.db import connection

from orders.models import Order, Refund

User = get_user_model()

CHANGE_REFUND = 'orders.change_refund'


def _proposed_refund(order_number='SEED-0002'):
    """AI 직원이 올린 환불 제안 한 건."""
    order = Order.objects.get(order_number=order_number)
    return Refund.objects.create(
        order=order,
        amount=order.total_amount,
        reason='고객 요청: 사이즈 불일치',
        requested_by=User.objects.get(username='ai-staff'),
    )


# ① 막은 것처럼 보이는 한 줄은 판정하지 않는다 — 답은 데이터에 있다


@pytest.mark.django_db
def test_권한은_코드가_아니라_auth_permission_행으로_존재한다(world):
    permission = Permission.objects.get(
        content_type__app_label='orders', codename='change_refund'
    )

    assert permission.pk  # 마이그레이션이 만들어 둔 행. 우리가 쓴 것이 아니다.

    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT COUNT(*) FROM auth_permission WHERE codename = %s', ['change_refund']
        )
        assert cursor.fetchone()[0] >= 1


@pytest.mark.django_db
def test_그룹이_자리고_그_자리에_권한이_붙어_있다(world):
    owner_codenames = set(
        Group.objects.get(name='점주').permissions.values_list('codename', flat=True)
    )
    ai_codenames = set(
        Group.objects.get(name='AI직원').permissions.values_list('codename', flat=True)
    )

    assert 'change_refund' in owner_codenames
    assert 'change_refund' not in ai_codenames
    # AI 직원은 제안까지다 — 만들 수는 있어도 바꿀 수는 없다.
    assert 'add_refund' in ai_codenames


@pytest.mark.django_db
def test_AI_직원에게는_승인_권한이_없고_점주에게는_있다(world):
    owner = User.objects.get(username='owner')
    ai_staff = User.objects.get(username='ai-staff')

    assert CHANGE_REFUND in owner.get_all_permissions()
    assert CHANGE_REFUND not in ai_staff.get_all_permissions()


@pytest.mark.django_db
def test_점주의_승인_권한은_전능함에서_오지_않는다(world):
    """superuser 라서가 아니라 그룹이 준 권한이라서 승인할 수 있다."""
    owner = User.objects.get(username='owner')

    assert not owner.is_superuser
    assert owner.is_staff
    assert owner.has_perm(CHANGE_REFUND)
    assert [group.name for group in owner.groups.all()] == ['점주']


@pytest.mark.django_db
def test_그룹에서_빼면_같은_코드가_다르게_판정한다(world):
    """법이 데이터라는 것의 결정적 증거 — 코드를 배포하지 않고 판정이 뒤집힌다."""
    owner = User.objects.get(username='owner')
    assert owner.has_perm(CHANGE_REFUND)

    Group.objects.get(name='점주').permissions.remove(
        Permission.objects.get(content_type__app_label='orders', codename='change_refund')
    )

    owner = User.objects.get(username='owner')  # 권한 캐시를 버리고 다시 읽는다
    assert not owner.has_perm(CHANGE_REFUND)


# ③ 정체성은 주장이 아니라 서버가 되찾아 오는 값이다


@pytest.mark.django_db
def test_로그인은_서명_세션_행을_남긴다(world, client):
    assert client.login(username='owner', password='owner1234')

    session_key = client.cookies['sessionid'].value
    decoded = Session.objects.get(session_key=session_key).get_decoded()

    assert decoded['_auth_user_id'] == str(User.objects.get(username='owner').pk)
    assert decoded['_auth_user_hash']  # SECRET_KEY 로 서명된 값


@pytest.mark.django_db
def test_나는_점주다_라고_말해도_request_user는_바뀌지_않는다(world, client):
    """요청 본문의 주장은 정체성이 아니다. 세션이 가리키는 계정만 정체성이다."""
    refund = _proposed_refund()
    assert client.login(username='ai-staff', password='ai1234')

    response = client.post(
        f'/orders/refunds/{refund.pk}/approve/',
        {'username': 'owner', 'role': '점주', 'is_superuser': 'true'},
    )

    assert response.status_code == 403
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROPOSED
    assert refund.decided_by is None


# ④ 공짜로 딸려 온 장부와, 아직 없는 장부


@pytest.mark.django_db
def test_admin_화면에서_고치면_장부가_자동으로_남는다(world, client):
    """django_admin_log 도 그 형식도 우리가 만든 적이 없다."""
    order = Order.objects.get(order_number='SEED-0002')
    assert client.login(username='owner', password='owner1234')

    response = client.post(
        f'/admin/orders/order/{order.pk}/change/',
        {
            'user': str(order.user_id),
            'status': Order.Status.SHIPPING,
            'recipient_name': order.recipient_name,
            'phone': order.phone,
            'address': order.address,
            'total_amount': str(order.total_amount),
            'items-TOTAL_FORMS': '0',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            '_save': '저장',
        },
        follow=True,
    )

    assert response.status_code == 200
    entry = LogEntry.objects.get()
    assert entry.user.username == 'owner'
    assert entry.action_flag == CHANGE
    assert entry.object_repr == str(order)


@pytest.mark.django_db
def test_승인_액션은_장부에_한_줄도_남기지_않는다(world, client):
    """Django 의 장부는 Django 가 자기 화면에서 한 변경만 적는다.

    우리 도메인 전이(결제 완료 → 취소)는 아무도 기록하지 않았다.
    `decided_by` 에 점주가 찍힌 것은 우리가 그 필드를 만들어 대입했기 때문이다.
    전이 자체의 장부는 7단계에서 짓는다 — 지금 메우지 말 것.
    """
    refund = _proposed_refund()
    assert client.login(username='owner', password='owner1234')

    response = client.post(
        '/admin/orders/refund/',
        {'action': 'approve_selected', '_selected_action': [str(refund.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    refund.refresh_from_db()
    assert refund.status == Refund.Status.APPROVED
    assert refund.decided_by.username == 'owner'
    assert Order.objects.get(order_number='SEED-0002').status == Order.Status.CANCELLED

    # 세계는 움직였는데 장부는 비어 있다 — 이것이 7단계의 출발점이다.
    assert LogEntry.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.skipif(
    connection.vendor != 'sqlite',
    reason='이 실측은 SQLite 의 성질(sqlite_master 에 남은 CREATE 문 원문)을 재는 것이다.',
)
def test_도메인_법은_아직_어디에도_시행되지_않는다(world):
    """7일·5만원은 사람이 말로 한 문장일 뿐, 코드에도 DB 에도 없다.

    4단계에서 이 규칙들을 이사시키면 이 테스트를 뒤집는 테스트가 생긴다.
    """
    assert not Permission.objects.filter(codename__icontains='refund_within').exists()
    assert not Permission.objects.filter(codename__icontains='escalate').exists()

    with connection.cursor() as cursor:
        cursor.execute("SELECT sql FROM sqlite_master WHERE name = 'orders_order'")
        create_sql = cursor.fetchone()[0].lower()

    # 금액에는 CHECK 가 붙어 있다 — PositiveIntegerField 한 단어가 딸고 온 범용 법이다.
    assert 'check ("total_amount" >= 0)' in create_sql
    # 그런데 status 에는 아무 계약도 없다. 어떤 값에서 어떤 값으로든 갈 수 있다.
    assert 'check' not in create_sql.split('"status"')[1].split(',')[0]
