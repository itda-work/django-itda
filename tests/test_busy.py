"""잠금 실패 판별 — `OperationalError` 라는 것만으로는 답이 안 된다.

itda-django 5단계가 세운 법을 패키지로 옮기며 그 시험도 함께 옮겼다.
저기서는 503 응답으로 확인했지만(HTTP 층), 여기서 재는 것은 판별 그 자체다.
"""

import sqlite3

import pytest
from django.contrib.auth.models import User
from django.db import OperationalError, connection, transaction

from django_itda.busy import is_lock_failure


def _with_cause(message, errorcode):
    """SQLite 가 코드까지 실어 준 진짜 잠금 실패의 모양."""
    cause = sqlite3.OperationalError(message)
    cause.sqlite_errorcode = errorcode
    failure = OperationalError(message)
    failure.__cause__ = cause
    return failure


def test_오류_코드가_BUSY_면_잠금이다():
    assert is_lock_failure(_with_cause('database is locked', sqlite3.SQLITE_BUSY))


def test_확장_오류_코드도_하위_8비트로_읽는다():
    # SQLITE_BUSY_SNAPSHOT 처럼 상위 바이트에 세부 사유가 실린 코드.
    extended = sqlite3.SQLITE_BUSY | (2 << 8)

    assert is_lock_failure(_with_cause('database is locked', extended))


def test_코드가_있으면_메시지는_보지_않는다():
    """코드가 잠금이 아니라고 말하면 메시지가 무엇이든 잠금이 아니다."""
    assert not is_lock_failure(_with_cause('database is locked', sqlite3.SQLITE_CORRUPT))


def test_코드가_없으면_알려진_메시지와_전체_일치():
    assert is_lock_failure(OperationalError('database is locked'))
    assert is_lock_failure(OperationalError('  Database table is locked  '))


def test_no_such_table_은_잠금이_아니다():
    """메시지에 `busy` 가 들어 있다고 잠금으로 세면 스키마 오류가 영원히 재시도된다."""
    assert not is_lock_failure(OperationalError('no such table: busy_orders'))


def test_부분_일치로_세지_않는다():
    assert not is_lock_failure(OperationalError('failed because database is locked, sorry'))


def test_OperationalError_가_아니면_잠금이_아니다():
    assert not is_lock_failure(ValueError('database is locked'))


# --- Postgres: sqlstate ---------------------------------------------------------


class _FakePgError(Exception):
    """psycopg 를 임포트하지 않는다 — 판별이 읽는 것은 `sqlstate` 속성 하나다."""

    def __init__(self, message, sqlstate):
        super().__init__(message)
        self.sqlstate = sqlstate


def _with_sqlstate(sqlstate, message='could not obtain lock'):
    failure = OperationalError(message)
    failure.__cause__ = _FakePgError(message, sqlstate)
    return failure


@pytest.mark.parametrize('sqlstate', ['55P03', '40P01', '40001'])
def test_PG_잠금_sqlstate_면_잠금이다(sqlstate):
    """lock_not_available·deadlock_detected·serialization_failure — 다시 보내면 될 요청."""
    assert is_lock_failure(_with_sqlstate(sqlstate))


def test_PG_unique_위반은_잠금이_아니다():
    assert not is_lock_failure(_with_sqlstate('23505', 'duplicate key value'))


def test_PG_statement_timeout_은_잠금이_아니다():
    """57014 는 느린 쿼리일 수 있다 — 재전송으로 번역하면 영원히 다시 보낸다."""
    timeout = _with_sqlstate('57014', 'canceling statement due to statement timeout')
    assert not is_lock_failure(timeout)


def test_sqlstate_가_있으면_메시지는_보지_않는다():
    assert not is_lock_failure(_with_sqlstate('23505', 'database is locked'))


def test_sqlstate_가_없는_원인이면_기존_판별_그대로():
    """원인에 `sqlstate` 가 없으면 SQLite 갈래(코드 → 메시지)로 간다."""
    failure = OperationalError('database is locked')
    failure.__cause__ = ValueError('sqlstate 없음')
    assert is_lock_failure(failure)
    assert not is_lock_failure(OperationalError('no such table: busy_orders'))


@pytest.mark.skipif(connection.vendor != 'postgresql', reason='Postgres 실측 — just test-pg')
@pytest.mark.django_db(transaction=True)
def test_PG_FOR_UPDATE_NOWAIT_경합은_잠금이다():
    """두 연결이 같은 행을 잠그려 하면 뒤쪽이 55P03 을 받는다 — 실측."""
    user = User.objects.create(username='잠긴-행')
    nowait = 'SELECT id FROM auth_user WHERE id = %s FOR UPDATE NOWAIT'
    other = connection.copy()
    try:
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            with pytest.raises(OperationalError) as raised, other.cursor() as cursor:
                cursor.execute(nowait, [user.pk])
    finally:
        other.close()

    assert raised.value.__cause__.sqlstate == '55P03'
    assert is_lock_failure(raised.value)
