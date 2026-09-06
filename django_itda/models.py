"""궤적 — 도구면의 호출 한 번이 한 행이다.

왜 이 모델이 있는가. 실접속 관찰 2차의 발견 3이다 — 점주가 `/admin/` 에서
환불을 승인했는데 `LogEntry` 는 **0건**이었다. 커스텀 admin action 은 장부를
남기지 않는다. 남는 것은 `Refund.decided_by=owner` · `decided_via='owner'`
한 줄뿐이고, **언제·어느 문으로** 는 어디에도 없다. `decided_via` 는
"무엇이 확정했나"(규칙인가 사람인가)의 답이지 경로의 답이 아니다.

그래서 `via` 열을 둔다. MCP·API·admin·shell 이 같은 표에 남아야
"누가 무엇을 언제 어느 문으로" 가 된다.

**도메인 전이의 장부와는 다른 층이다.** 저쪽은 "세계가 무엇으로 바뀌었나" 를
남기고, 이쪽은 "누가 무엇을 시켰나" 를 남긴다. 거부된 호출·잠긴 호출·터진
호출은 세계를 바꾸지 않았으므로 도메인 장부에는 남을 것이 없지만, **불린
적은 있다** — 그 사실이 여기 남는다. 둘은 `call_id` 로 잇는다.

append-only 다. 고치거나 지우지 않는다(admin 도 읽기 전용이다).
"""

from django.conf import settings
from django.db import models


class ToolCall(models.Model):
    """도구 호출 한 번."""

    class Via(models.TextChoices):
        """어느 문으로 들어왔나."""

        MCP = 'mcp', 'MCP 도구면'
        API = 'api', 'HTTP API'
        ADMIN = 'admin', 'admin'
        SHELL = 'shell', 'shell'

    class Error(models.TextChoices):
        """끝난 갈래. 빈 값이면 결과를 돌려줬다는 뜻이다."""

        FORBIDDEN = 'forbidden', '권한 없음'
        DENIED = 'denied', '세계가 거부'
        BUSY = 'busy', '잠금 실패'
        EXCEPTION = 'exception', '예외'

    call_id = models.CharField('호출 ID', max_length=32, unique=True)
    tool = models.CharField('도구', max_length=100)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='자리',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tool_calls',
    )
    via = models.CharField('경로', max_length=20, choices=Via.choices)
    arguments = models.JSONField('인자', default=dict, blank=True)
    # 판정·결과는 **판정에 닿았을 때만** 채운다. 권한 거부·잠금 실패·예외는
    # 빈 값이다 — 판정한 적이 없는 호출에 판정 어휘를 적으면 장부가 거짓말한다.
    kind = models.CharField('판정', max_length=20, blank=True, default='')
    outcome = models.CharField('결과', max_length=20, blank=True, default='')
    rule_ids = models.JSONField('규칙 ID', default=list, blank=True)
    reason = models.TextField('사유', blank=True, default='')
    error = models.CharField(
        '오류 갈래', max_length=20, choices=Error.choices, blank=True, default=''
    )
    started_at = models.DateTimeField('시작 시각')
    duration_ms = models.PositiveIntegerField('소요(ms)', default=0)

    class Meta:
        verbose_name = '도구 호출'
        verbose_name_plural = '도구 호출'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.tool} / {self.actor} / {self.kind or self.error}'
