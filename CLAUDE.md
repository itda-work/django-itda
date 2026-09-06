# CLAUDE.md

Django 실행세계 교육용 단계별 프로젝트. 설계 정본은 [docs/단계별-설계.md](docs/단계별-설계.md), 단계 색인은 [stages/README.md](stages/README.md).

## 기본

- 응답·문서·커밋 메시지는 한국어 우선. 코드 주석도 한국어.
- 위치 `~/Apps/itda-work/itda-django` (2026-09-06 itda-skills 에서 이동, 같은 날 `hyve-django` 에서 `itda-django` 로 개명). 형제 패키지 `~/Apps/django-itda/`(도구면 패키지)와 이름이 거울상이니 섞지 말 것 — 교육 프로젝트가 itda-django, 패키지가 django-itda. 프로젝트 지식은 저장소 안에 자족적으로 둔다(개인 메모리 의존 0).
- 툴 파라미터의 한글은 리터럴 UTF-8. `\uXXXX` 이스케이프 금지.
- 스택: Python 3.12+ · uv · Django 5.2 LTS · SQLite · pytest-django · ruff. 8단계 전 외부 서비스 0.
- **한 단계 = 법 하나.** 단계 경계를 넘는 기능을 미리 넣지 않는다(다음 단계의 "의도적 실패"가 준비된 결함이다 — 고치지 말 것).
- AI 직원은 고정 fixture. 실제 LLM 호출 코드를 넣지 않는다.
- `RULES.md`(규칙 대장)는 append-only — 행 삭제 금지, `deprecated` + 후속 버전만.
- 태그 `stage-NN-start` / `stage-NN-done`. 히스토리 재작성 금지, 태그 이동은 `stages/README.md`에 기록.

## 구현 위임

코드 구현은 `.claude/agents/app-builder.md`(Opus · medium)에 위임한다. 메인 세션 상위 모델로 직접 구현하지 않는다.

## 검증

- 단계 완료 전 `just test NN` 통과 + `stage-NN-start`에서 같은 테스트가 실패하는지 확인.
- `db.sqlite3`·`.venv`·`.env`는 커밋하지 않는다.
