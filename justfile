# django-itda — Django 실행세계 도구면 패키지 (저장소 루트)
# 교육 프로젝트의 레시피는 examples/itda-django/justfile 에 있다.

# 명령 목록
default:
    @just --list

# 예시(교육 프로젝트) 테스트 — 첫 사용자가 통과해야 패키지가 산다
test:
    cd examples/itda-django && just test

# 예시 justfile 로 그대로 전달 — 예: just example run / just example token
example *ARGS:
    just -f examples/itda-django/justfile -d examples/itda-django {{ ARGS }}

# 패키지 테스트를 Postgres 로 — 컨테이너는 예시 쪽 `just example pg-up` 이 띄운다
test-pg *ARGS:
    DJANGO_SETTINGS_MODULE=tests.pg_settings uv run --with 'psycopg[binary]' pytest {{ ARGS }}
