"""패키지 테스트의 Postgres 실측용 설정 — 루트 `just test-pg` 가 쓴다.

`tests.settings` 를 상속하고 DB 만 예시 프로젝트와 같은 임시 컨테이너
(`itda-django-pg`, 127.0.0.1:55433 — `examples/itda-django` 의 `just pg-up`)로 바꾼다.
SQLite 용 `TEST.NAME`(파일 경로)이 따라오지 않게 `DATABASES` 를 통째로 교체한다.
"""

from tests.settings import *  # noqa: F401,F403

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
