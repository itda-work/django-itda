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
적는다 — DB 역할 권한(PostgreSQL `REVOKE DELETE`)은 8단계 소재다. PostgreSQL 에서는
`REVOKE DELETE`·`BEFORE DELETE` 트리거로 닫을 수 있다(운영 소재 — 이번엔 안 막는다).

## 사후 변경(2026-09-19, PG 프로브) — 백엔드별 분기

원래 `RunSQL` 한 줄이었다. 그 SQL(`BEGIN SELECT RAISE(...); END;`)은 SQLite 문법이라
PostgreSQL 에서는 **migrate 자체가 실패**했다(`syntax error at or near "BEGIN"`,
루트 `docs/postgres-실측-2026-09-19.md` §1). 그래서 `RunPython` 으로 바꾸고
`schema_editor.connection.vendor` 로 가른다.

- `sqlite` — 종전 SQL 그대로다. **만들어지는 스키마는 바이트 단위로 같다**
  (`sqlite_master` 의 트리거 `sql` 을 바꾸기 전·후 새 DB 에서 대조했다). 이미 적용된
  마이그레이션 파일을 고치는 것이지만, 이미 적용된 DB(dev DB 포함)는 다시 돌지 않고
  새로 만드는 DB 는 같은 트리거를 얻는다 — 기존 DB 에 영향이 없다.
- `postgresql` — 트리거 함수가 `RAISE EXCEPTION ... USING ERRCODE = '23000'`
  (`integrity_constraint_violation`)를 던진다. psycopg 는 SQLSTATE 23 계열을
  `IntegrityError` 로 올리고 Django 도 `IntegrityError` 로 감싼다 — SQLite 와 같은 예외다.
- 그 밖의 vendor — **아무것도 만들지 않고 넘어간다.** 그 DB 에서는 모델 층 거부만
  남는다(`QuerySet.update()`·raw SQL 은 열려 있다).
"""

from django.db import migrations

# SQLite — 종전 `RunSQL` 이 실행하던 문장 그대로다. `RunSQL` 은 문자열을 sqlparse 로
# 쪼개며 앞뒤 공백과 끝의 `;` 를 떼고 실행했다. 같은 문장이 들어가야
# `sqlite_master.sql` 이 바이트 단위로 같으므로 여기서도 똑같이 뗀다.
SQLITE_CREATE = """
CREATE TRIGGER ledger_event_no_update
BEFORE UPDATE ON ledger_event
BEGIN
    SELECT RAISE(ABORT, 'LEDGER-001@v1: 장부는 고치지 않는다');
END;
"""

SQLITE_DROP = 'DROP TRIGGER IF EXISTS ledger_event_no_update;'

# PostgreSQL — 트리거 본문은 함수여야 한다. ERRCODE 23000 은
# `integrity_constraint_violation` 이라 Django 에서 `IntegrityError` 로 올라온다.
POSTGRES_CREATE = (
    """
CREATE FUNCTION ledger_event_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'LEDGER-001@v1: 장부는 고치지 않는다' USING ERRCODE = '23000';
END;
$$
""",
    """
CREATE TRIGGER ledger_event_no_update
BEFORE UPDATE ON ledger_event
FOR EACH ROW EXECUTE FUNCTION ledger_event_no_update()
""",
)

POSTGRES_DROP = (
    'DROP TRIGGER IF EXISTS ledger_event_no_update ON ledger_event',
    'DROP FUNCTION IF EXISTS ledger_event_no_update()',
)


def _statement(sql):
    """`RunSQL` 이 하던 대로 앞뒤 공백과 끝의 `;` 를 뗀다."""
    return sql.strip().removesuffix(';').rstrip()


def forward(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == 'sqlite':
        schema_editor.execute(_statement(SQLITE_CREATE))
    elif vendor == 'postgresql':
        for sql in POSTGRES_CREATE:
            schema_editor.execute(_statement(sql))
    # 그 밖의 vendor: 아무것도 만들지 않는다 — 모델 층 거부만 남는다(모듈 docstring).


def backward(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == 'sqlite':
        schema_editor.execute(_statement(SQLITE_DROP))
    elif vendor == 'postgresql':
        for sql in POSTGRES_DROP:
            schema_editor.execute(_statement(sql))


class Migration(migrations.Migration):
    dependencies = [('ledger', '0001_initial')]

    operations = [migrations.RunPython(forward, backward)]
