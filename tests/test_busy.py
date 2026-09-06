"""잠금 실패 판별 — `OperationalError` 라는 것만으로는 답이 안 된다.

itda-django 5단계가 세운 법을 패키지로 옮기며 그 시험도 함께 옮겼다.
저기서는 503 응답으로 확인했지만(HTTP 층), 여기서 재는 것은 판별 그 자체다.
"""

import sqlite3

from django.db import OperationalError

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
