# django-itda — Django 실행세계 도구면 (working title)

> **정체**: Django 앱을 AI 에이전트의 **판정 있는 도구면**으로 노출하는 패키지.
> 도구 호출 하나하나가 Django의 `request.user`·permission을 1급 입력으로 받아 **ALLOW / DENY / ESCALATE** 3값으로 판정되고, ESCALATE는 사람 승인을 기다리는 핸들이 되며, 모든 호출은 Django ORM에 궤적으로 남는다.
> 관통 논지 — **제안은 AI, 판정은 세계, 확정은 사람**(격상 건). 사람이 승인한 정책 범위는 규칙이 자동 확정한다.

- 상태: 설계 단계 (2026-09-06). 첫 사용자는 [hyve-django](../itda-work/hyve-django/)(교육용 단계별 실행세계)의 실접속 트랙.
- 정본 문서: [docs/벤치마킹-MCP-생태계.md](docs/벤치마킹-MCP-생태계.md)(감쌀 것/쓸 것 결정 근거) · [docs/설계.md](docs/설계.md)

## 무엇을 만드는가 (진짜 공백 — 벤치마킹 §7.3)

1. **판정기** — Django User/Permission을 입력으로 하는 `Verdict(ALLOW|DENY|ESCALATE, rule_ids, reason, alternatives)` 계약과 도구 결과 매핑(DENY = 실행 오류로 모델 자기수정 가능, ESCALATE = 구조화 결과 + 승인 핸들).
2. **격상(ESCALATE) ↔ 사람 승인** — 승인 핸들 모델 + admin 승인 화면 결합. MCP Tasks(`input_required`) 지원 클라이언트에는 Tasks로, 아니면 `get_approval_status` 폴링 핸들 폴백.
3. **궤적(trajectory)** — 도구 호출·판정·결정 규칙·확정자를 append-only ORM 이벤트로(상관 ID). admin 열람.
4. **권한 = 가시성** — 권한 없는 도구는 `tools/list`에서부터 제외되고 직접 호출도 차단(같은 검사).
5. **Django 브리지** — stdio(`manage.py mcp_stdio`, 배포 변경 0)·Streamable HTTP(무상태 코어에 맞춘 뷰 브리지, WSGI 겸용)·세션 쿠키/토큰 인증 어댑터.

## 만들지 않는 것 (벤치마킹 §7.4)

전송·프로토콜 구현, OAuth 2.1 RS, 스코프 가시성 엔진, 스키마 생성, 미들웨어 파이프라인, 태스크 상태머신, OpenTelemetry, OpenAPI 변환 — 전부 `mcp` v2 / `fastmcp` 4.x가 이미 제공. **"Django가 FastAPI에 밀리는 기능 종합 패키지"는 명시적 비목표**(django-ninja·코어 로드맵의 영역, 우리 논지와도 충돌).

## 아키텍처 결정 (권고 B-1)

- 기반: **fastmcp 4.x**(mcp v2, 2026-07-28 스펙). 권한 필터(`auth=`)·궤적 훅(`Middleware.on_call_tool`)·Tasks(`fastmcp-tasks`)가 안정 공개 API.
- 격리: fastmcp 의존은 `django_itda.adapters.fastmcp` **한 모듈**에만. 판정 계약·궤적 모델·승인 핸들·도구 선언은 fastmcp 타입에 의존하지 않는다(메이저 판올림 6~10개월 주기 대비).
- 기각: django-mcp-server 위 확장(A) — mcp v1 고정, 마지막 커밋 2026-03, v2 마이그레이션 PR 미머지.

## 열린 결정

- 패키지/임포트 이름(`django-itda` 는 작업명).
- Tasks 클라이언트 지원 매트릭스(Claude Desktop/Code, ChatGPT) — 미확인 → 폴백 핸들이 기본.
