#!/usr/bin/env bash
# 실접속(live) 트랙 — 토큰 발급 + Claude Code MCP 재등록을 한 번에 한다.
#
# 사람이 원문 키를 손으로 옮기지 않게 하는 것이 목적이다. 키는 화면에 찍지 않고
# 이 프로세스 안에서만 흐른다. Claude Code 의 local 스코프 등록은 명령을 실행한
# cwd 의 프로젝트에 묶이므로, 등록은 반드시 저장소 루트에서 한다 — 새 세션을 여는
# 곳이 거기다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
EX="$ROOT/examples/itda-django"

if ! command -v claude >/dev/null 2>&1; then
    echo "오류: claude 명령을 찾을 수 없습니다. Claude Code CLI 를 설치한 뒤 다시 실행하세요." >&2
    exit 1
fi

# 1) 열쇠를 새로 만든다. 원문 키는 이 출력에서 딱 한 번 나온다 — 파이프로 바로 받는다.
cd "$EX"
RAW=$(uv run python manage.py issue_token ai-staff --name claude-code | sed -n 's/^ *WORLD_TOKEN=//p')
if [ -z "$RAW" ]; then
    echo "오류: 토큰 발급에 실패했습니다. just setup 으로 세계를 먼저 심었는지 확인하세요." >&2
    exit 1
fi
echo "  열쇠를 발급했습니다 (계정 ai-staff · 용도 claude-code). 값은 화면에 찍지 않습니다."

# 2) 저장소 루트에서 두 문을 다시 건다. 지우기는 없어도 실패가 아니다.
cd "$ROOT"
claude mcp remove itda-world -s local >/dev/null 2>&1 || true
claude mcp remove itda-world-pkg -s local >/dev/null 2>&1 || true

claude mcp add itda-world-pkg -s local --env WORLD_TOKEN="$RAW" \
    -- uv --directory "$EX" run python manage.py mcp_stdio
claude mcp add itda-world -s local --env WORLD_TOKEN="$RAW" \
    -- uv --directory "$EX" run python agent/live/mcp_server.py

# 3) 두 줄이 보이는지 확인한다.
echo
claude mcp list 2>/dev/null | grep -E 'itda-world' || true

echo
echo "저장소 루트($ROOT)에서 새 Claude Code 세션을 열면 도구가 붙습니다."
echo "세계가 더러워지면 just live-setup, 키만 잃었으면 just live-register."
