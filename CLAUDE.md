# CLAUDE.md

Django 실행세계 도구면 패키지. 정체·비목표는 [README.md](README.md), 근거는 [docs/벤치마킹-MCP-생태계.md](docs/벤치마킹-MCP-생태계.md).

- 응답·문서·커밋 메시지 한국어 우선. 툴 파라미터의 한글은 리터럴 UTF-8(`\uXXXX` 금지).
- 프로젝트 지식은 저장소 안에 자족적으로(개인 메모리 의존 0).
- **fastmcp 의존은 `django_itda/adapters/fastmcp*.py` 에만.** 핵심(판정·궤적·승인 핸들·도구 선언)은 순수 Django.
- 스택: Python 3.12+ · uv · Django 5.2 LTS ~ 6.1 · fastmcp 4.x(선택 extra) · pytest-django · ruff.
- 비목표(README)를 넘는 기능은 넣지 않는다 — 전송·OAuth·스키마 생성은 fastmcp/mcp 것을 쓴다.
- 이 저장소의 루트가 곧 패키지다(`django_itda/`). 교육 프로젝트는 `examples/itda-django/`(자체 `CLAUDE.md` 가 그쪽 정본)에 uv workspace 멤버로 산다 — `[tool.uv.workspace] members = ["examples/*"]`.
- 코드 구현은 `.claude/agents/app-builder.md`(Opus · medium)에 위임한다. 메인 세션 상위 모델로 직접 구현하지 않는다.
- 첫 사용자인 `examples/itda-django/` 의 실접속 트랙에서 실제로 소비되지 않는 API 는 만들지 않는다.
- 저장소 구조 이력은 [docs/구조-뒤집기-2026-09-06.md](docs/구조-뒤집기-2026-09-06.md).
