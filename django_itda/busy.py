"""세계가 잠겨서 **판정하지 못한** 것은 판정이 아니다.

itda-django `config/middleware.py` 에서 판별부만 뽑아 왔다. 거기서는 뷰의 예외를
503 + `Retry-After` 로 옮기는 미들웨어가 이것을 썼는데, 도구면은 HTTP 를 지나지
않는다(in-process). 그래서 여기 남는 것은 **무엇이 잠금 실패인가** 하나이고,
그 답을 무엇으로 번역하는지는 부르는 쪽이 정한다 — 도구면은 `ToolBusy` 로 옮긴다.

409 는 "자격은 있지만 세계가 지금 그 상태가 아니다" 라는 **판정**이다.
`database is locked` 는 판정이 아니라 **판정에 실패한 것**이다. 둘을 섞으면
세계가 거짓말을 한다 — 다시 보내면 될 요청에 대고 규칙 위반이라고 답하면,
AI 직원은 있지도 않은 규칙을 고객에게 설명한다.

## 무엇이 잠금 실패인가 — 문자열로 세지 않는다

`OperationalError` 는 SQLite 가 내는 온갖 것을 다 담는다. `no such table:
busy_orders` 도 `OperationalError` 이고, 메시지에 `busy` 가 들어 있다.
그걸 잠금으로 세면 **스키마 오류를 "잠시 후 다시 보내라" 로 번역**하는 것이고,
클라이언트는 영원히 다시 보낸다.

그래서 판별은 두 단계다.

1. 원인 예외(`__cause__`)의 `sqlite_errorcode` 가 `SQLITE_BUSY`·`SQLITE_LOCKED` 인가.
   확장 오류 코드는 하위 8비트에 기본 코드를 담으므로 `& 0xFF` 로 벗긴다.
2. 코드가 없으면(다른 백엔드·주입 테스트) 알려진 **정확한 메시지**와 일치하는가.
   부분 문자열이 아니라 전체 일치다.

## Postgres — `sqlstate` 로 센다

psycopg 는 원인 예외에 SQLSTATE 다섯 자리를 `sqlstate` 로 싣는다. 잠금 실패로 세는
것은 **같은 요청을 다시 보내면 통할 수 있는** 셋뿐이다 — `55P03`(lock_not_available,
`NOWAIT`·`lock_timeout`), `40P01`(deadlock_detected), `40001`(serialization_failure).
셋 다 트랜잭션이 통째로 물러났으니 재전송이 곧 올바른 재시도다.
`57014`(query_canceled — `statement_timeout`)는 넣지 않는다. 잠금 대기가 아니라 **느린
쿼리**일 수 있고, 그걸 "잠시 후 다시" 로 번역하면 같은 느린 쿼리를 영원히 다시 보낸다.
psycopg 는 임포트하지 않는다 — 속성만 읽으므로 패키지는 드라이버를 모른 채 선다.
"""

import sqlite3

from django.db import OperationalError

BUSY_REASON = (
    '세계가 잠겨 있어 이 요청은 판정하지 못했다 — 거부(409)가 아니다. '
    '잠시 후 같은 요청을 다시 보내라.'
)
RETRY_AFTER = 1

# SQLite 가 "지금은 못 준다" 고 말하는 두 코드.
BUSY_CODES = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)

# 오류 코드를 못 얻었을 때만 쓰는 대조표. 부분 일치가 아니라 전체 일치다.
LOCK_MESSAGES = ('database is locked', 'database table is locked')

# Postgres 가 "지금은 못 준다(다시 보내라)" 고 말하는 SQLSTATE 셋. 57014 는 뺐다(모듈 설명).
LOCK_SQLSTATES = ('55P03', '40P01', '40001')


def is_lock_failure(exception):
    """잠금 실패인가. `OperationalError` 라는 것만으로는 답이 안 된다."""
    if not isinstance(exception, OperationalError):
        return False
    for source in (exception.__cause__, exception):
        sqlstate = getattr(source, 'sqlstate', None)
        if sqlstate is not None:
            return sqlstate in LOCK_SQLSTATES
    code = getattr(exception.__cause__, 'sqlite_errorcode', None)
    if code is not None:
        return code & 0xFF in BUSY_CODES
    return str(exception).strip().lower() in LOCK_MESSAGES
