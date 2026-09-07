"""장부 — **세계가 실제로 바뀐 사실**만 적는다 (7단계).

`django_itda.ToolCall`(궤적)과는 층이 다르다. 저쪽은 "누가 무엇을 시켰나" 이고
여기는 "세계가 무엇으로 바뀌었나" 다. 거부(DENY)·재전송(REPLAYED)·기존 건
응답(ALREADY)은 세계를 바꾸지 않았으므로 **여기 없다** — 불린 사실은 궤적의
몫이다. 둘은 `call_id` 로 잇는다.

세 가지 규율이 이 파일의 전부다.

1. **사실 뒤에** 적는다 — 의도가 아니라 일어난 일을 적는다.
2. **같은 트랜잭션**에서 적는다 — 사실이 롤백되면 기록도 롤백된다.
3. **고칠 수 없다** — 모델 층(`save`·`delete`)이 거부하고, DB 층(트리거)이 막는다.

세 번째가 두 겹인 이유는 모델 층이 `QuerySet.update()` 를 못 막기 때문이다.
`Event.objects.filter(...).update(reason='...')` 는 `save()` 를 지나지 않는다 —
그래서 마이그레이션이 DB 트리거를 건다.

## 장부 자체가 오염 벡터다

`reason` 에는 **세계의 판정 문장만** 넣는다. 고객·모델이 쓴 원문(환불 사유·제안
문구)은 넣지 않는다. 장부는 나중에 사람이 읽고 믿는 자리라서, 거기에 남의 말을
그대로 실으면 "장부에 이렇게 적혀 있다" 가 공격면이 된다. 원문이 필요하면
`Refund.reason` 에 있다.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from django_itda.context import current_call


class LedgerImmutable(Exception):
    """장부를 고치거나 지우려 했다 (LEDGER-001@v1).

    `IntegrityError` 의 서브클래스가 **아니다.** 무결성 위반이 아니라 이 세계의
    법이고, 둘을 섞으면 `except IntegrityError` 로 조용히 삼켜진다.
    """


class EventQuerySet(models.QuerySet):
    """`delete()` 를 닫은 QuerySet — `Event.objects.all().delete()` 도 여기서 걸린다.

    `update()` 는 여기서 막지 않는다. 막을 수는 있지만 그러면 "모델 층이
    지킨다" 는 거짓 안심이 된다 — raw SQL 은 이 클래스를 지나지 않는다.
    갱신을 막는 것은 DB 트리거의 몫이고(`0002_append_only_trigger`), 그래서
    이 자리는 비워 둔다.
    """

    def delete(self):
        raise LedgerImmutable('LEDGER-001@v1: 장부는 지우지 않는다.')


class Event(models.Model):
    """장부 한 줄 = 세계가 바뀐 사실 하나."""

    class Door(models.TextChoices):
        """어느 문으로 들어온 요청이 이 사실을 만들었나.

        실접속 관찰 2차 **발견 3** 의 답이다 — 점주가 admin 에서 승인했을 때
        `LogEntry` 는 0건이었고, 어느 문으로 들어왔는지는 아무 데도 없었다.
        """

        MCP = 'mcp', 'MCP 도구면'
        API = 'api', 'HTTP API'
        CONSOLE = 'console', '콘솔'
        ADMIN = 'admin', 'admin'
        CUSTOMER = 'customer', '고객의 문'
        SHELL = 'shell', 'shell'

    class Kind(models.TextChoices):
        """그 사실을 만든 판정. **DENY 는 없다** — 거부는 세계를 바꾸지 않는다."""

        ALLOW = 'ALLOW', '허용'
        ESCALATE = 'ESCALATE', '격상'

    class Transition(models.TextChoices):
        """무엇이 일어났나. 이름은 `<대상>.<과거형>` — 사실이지 명령이 아니다."""

        ORDER_PLACED = 'order.placed', '주문 접수'
        ORDER_PAID = 'order.paid', '결제 완료'
        ORDER_CANCELLED = 'order.cancelled', '주문 취소'
        STOCK_DEDUCTED = 'stock.deducted', '재고 차감'
        REFUND_PROPOSED = 'refund.proposed', '환불 제안'
        REFUND_APPROVED = 'refund.approved', '환불 승인'
        REFUND_REJECTED = 'refund.rejected', '환불 거부'
        PAYMENT_LINK_ISSUED = 'payment_link.issued', '결제 링크 발급'

    at = models.DateTimeField('기록 시각', default=timezone.now, db_index=True)
    # `auto_now_add` 가 아니다 — 그러면 시각을 INSERT 가 정하고, 전이 메서드가
    # 정한 시각과 미세하게 갈린다. 장부의 시각은 사실의 시각이어야 한다.
    door = models.CharField('문', max_length=20, choices=Door.choices, blank=True, default='')
    call_id = models.CharField('호출 ID', max_length=32, blank=True, default='', db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='자리',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ledger_events',
    )
    # 계정이 지워져도 이름은 남는다. `SET_NULL` 뒤에 "누가" 가 통째로 사라지면
    # 그건 장부가 아니라 외래키다.
    actor_label = models.CharField('자리 이름', max_length=50, blank=True, default='')
    subject_type = models.ForeignKey(
        ContentType, verbose_name='대상 종류', on_delete=models.PROTECT
    )
    subject_id = models.PositiveIntegerField('대상 ID')
    subject = GenericForeignKey('subject_type', 'subject_id')
    subject_label = models.CharField('대상', max_length=100, blank=True, default='')
    transition = models.CharField('전이', max_length=50, choices=Transition.choices)
    kind = models.CharField('판정', max_length=20, choices=Kind.choices)
    # 판정 각인 — 어떤 규칙이 이 사실을 허락·격상했나.
    rule_ids = models.JSONField('규칙 ID', default=list, blank=True)
    reason = models.TextField('사유', blank=True, default='')
    before = models.JSONField('이전', default=dict, blank=True)
    after = models.JSONField('이후', default=dict, blank=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        verbose_name = '장부'
        verbose_name_plural = '장부'
        # 장부는 **시간 순**으로 읽는다. 궤적(`-started_at`)과 반대인 것이 의도다 —
        # 저쪽은 "방금 무슨 호출이 있었나", 여기는 "이 주문의 일생" 이다.
        ordering = ['at', 'id']
        indexes = [models.Index(fields=['subject_type', 'subject_id'])]

    def __str__(self):
        return f'{self.transition} · {self.subject_label}'

    def save(self, *args, **kwargs):
        """이미 있는 행은 다시 저장되지 않는다 — 고치는 것도 저장이다."""
        if self.pk is not None:
            raise LedgerImmutable('LEDGER-001@v1: 장부는 고치지 않는다.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise LedgerImmutable('LEDGER-001@v1: 장부는 지우지 않는다.')

    @classmethod
    def record(
        cls,
        *,
        subject,
        transition,
        kind,
        rule_ids=(),
        reason='',
        before=None,
        after=None,
        actor=None,
    ):
        """장부 한 줄을 적는다. **부르는 자리가 계약이다.**

        `door`·`call_id` 는 인자로 받지 않는다 — 호출 문맥에서 읽는다
        (`django_itda.context.current_call()`). 도메인 코드가 "나는 MCP 로
        불렸다" 를 인자로 나르기 시작하면 모든 전이 메서드에 전송 계층이
        스며든다. 문맥은 문이 묶고, 장부는 읽기만 한다.

        `actor` 만 **명시 인자**다. 토큰 인증이 뷰 안에서 일어나 미들웨어
        시점에는 자리를 모르기 때문이다 — 반쯤 아는 것을 문맥에 묶으면
        장부가 문마다 다른 정확도를 갖는다.

        규칙이 자동 확정한 사실에는 자리가 없다(`actor=None`). 그것은 빠진
        값이 아니라 **사람이 없었다는 사실**이다.
        """
        call = current_call()
        return cls.objects.create(
            door=call.via,
            call_id=call.call_id,
            actor=actor if getattr(actor, 'pk', None) else None,
            actor_label=str(getattr(actor, 'username', '') or '')[:50],
            subject=subject,
            subject_label=str(subject)[:100],
            transition=transition,
            kind=kind,
            rule_ids=list(rule_ids),
            reason=reason,
            before=before or {},
            after=after or {},
        )
