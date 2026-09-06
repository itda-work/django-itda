# itda-django — 단계별 실행세계

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

# 경합을 눈으로 본다 — 같은 요청 두 개를 동시에 쏜다 (refund / pay / approve)
race MODE TARGET *ARGS:
    uv run python manage.py race {{ MODE }} {{ TARGET }} {{ ARGS }}

# 공유 세계 — 옆자리 학생이 내 세계에 요청을 보낼 수 있게 문을 연다 (5단계)
run-shared:
    WORLD_SHARED=1 uv run python manage.py runserver 0.0.0.0:8000

# 실접속 트랙 — API 토큰 발급 (원문 키는 여기서 한 번만 나온다)
token USERNAME='ai-staff' NAME='claude-code':
    uv run python manage.py issue_token {{ USERNAME }} --name {{ NAME }}

# 실접속 트랙 — MCP 서버(stdio). 별도 프로세스다. just run 이 떠 있어야 한다
mcp:
    uv run python agent/live/mcp_server.py

# DB 를 지우고 세계를 처음부터 다시 심는다
reset-db:
    rm -f db.sqlite3
    uv run python manage.py migrate
    uv run python manage.py seed_world
