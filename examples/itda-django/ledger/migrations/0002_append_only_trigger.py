"""장부는 고칠 수 없다 — **DB 층** (LEDGER-001@v1).

모델 층(`Event.save`/`delete`)이 못 막는 것이 하나 있다. `QuerySet.update()` 다 —
`Event.objects.filter(...).update(reason='...')` 는 `save()` 를 지나지 않는다.
raw SQL 은 더 말할 것도 없다. 그래서 갱신을 막는 자리는 DB 다.

`RAISE(ABORT, ...)` 는 `SQLITE_CONSTRAINT` 라 Python `sqlite3.IntegrityError` 로,
Django 에서는 `IntegrityError` 로 올라온다. 우리 예외(`LedgerImmutable`)가 아닌
것이 의도다 — 막은 것이 우리 코드가 아니라 **DB** 이기 때문이다.

## DELETE 트리거는 걸지 않는다

Django 의 테스트 DB flush 가 `DELETE FROM` 을 쓴다. 걸면 teardown 이 깨진다.
삭제는 모델 층만 막고, 그 한정을 `RULES.md` 의 "우회 가능 경로" 열에 정직하게
적는다 — DB 역할 권한(PostgreSQL `REVOKE DELETE`)은 8단계 소재다.
"""

from django.db import migrations

CREATE = """
CREATE TRIGGER ledger_event_no_update
BEFORE UPDATE ON ledger_event
BEGIN
    SELECT RAISE(ABORT, 'LEDGER-001@v1: 장부는 고치지 않는다');
END;
"""

DROP = 'DROP TRIGGER IF EXISTS ledger_event_no_update;'


class Migration(migrations.Migration):
    dependencies = [('ledger', '0001_initial')]

    operations = [migrations.RunSQL(CREATE, DROP)]
