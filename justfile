# hyve-django — 단계별 실행세계

# 명령 목록
default:
    @just --list

# 의존성 설치 → 마이그레이션 → 세계 심기
setup:
    uv sync
    uv run python manage.py migrate
    uv run python manage.py seed_world

# 개발 서버
run:
    uv run python manage.py runserver

# 테스트 — NN 을 주면 그 단계만, 없으면 전체
test NN='':
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "{{ NN }}" ]; then
        uv run pytest
    else
        uv run pytest tests/stage_{{ NN }}_*.py
    fi

# 단계 시작 상태로 이동
stage NN:
    git checkout stage-{{ NN }}-start

# DB 를 지우고 세계를 처음부터 다시 심는다
reset-db:
    rm -f db.sqlite3
    uv run python manage.py migrate
    uv run python manage.py seed_world
