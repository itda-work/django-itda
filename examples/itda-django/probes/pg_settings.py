"""Postgres 실측용 설정 — `just pg-up`·`just test-pg`·`just migrate-pg` 가 쓴다.

`config.settings` 를 그대로 상속하고 DB 만 임시 Postgres 컨테이너
(`itda-django-pg`, 127.0.0.1:55433)로 바꾼다. 선례는 `../itda-django-bridge/probes/pg_settings.py`.

`DATABASES` 는 **통째로** 갈아 끼운다. 상속한 `default` 를 고쳐 쓰면 SQLite 전용
`OPTIONS`(`transaction_mode`·`init_command`)와 `TEST.NAME`(파일 경로)이 따라와서
psycopg 가 모르는 옵션으로 연결에 실패한다 — 재는 것이 PG 가 아니라 설정 찌꺼기가 된다.
"""

from config.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'itda',
        'USER': 'itda',
        'PASSWORD': 'itda',
        'HOST': '127.0.0.1',
        'PORT': '55433',
    }
}
