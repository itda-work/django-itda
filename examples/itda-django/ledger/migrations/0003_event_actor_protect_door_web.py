"""장부에 남은 계정은 지울 수 없다 — `SET_NULL` → `PROTECT`. 그리고 문 표에 `web`.

`SET_NULL` 은 계정 삭제 때 장부 행을 UPDATE 하려 들고, `0002` 의 append-only
트리거가 그것을 거절한다(`IntegrityError`). 장부 불변과 계정 삭제는 동시에
성립하지 않으므로 계약을 정한다 — **장부가 이긴다**(`ProtectedError`).
익명화·삭제 정책은 8단계.

`web` 은 미들웨어의 기본 문이다. 표에 없는 값이 데이터로 들어오고 있었다.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ledger', '0002_append_only_trigger'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='event',
            name='actor',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name='ledger_events',
                to=settings.AUTH_USER_MODEL,
                verbose_name='자리',
            ),
        ),
        migrations.AlterField(
            model_name='event',
            name='door',
            field=models.CharField(
                blank=True,
                choices=[
                    ('mcp', 'MCP 도구면'),
                    ('api', 'HTTP API'),
                    ('console', '콘솔'),
                    ('admin', 'admin'),
                    ('customer', '고객의 문'),
                    ('shell', 'shell'),
                    ('web', '문 표 밖'),
                ],
                default='',
                max_length=20,
                verbose_name='문',
            ),
        ),
    ]
