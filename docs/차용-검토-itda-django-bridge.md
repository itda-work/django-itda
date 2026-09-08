# 차용 검토 — `../itda-django-bridge` 에서 이 저장소로 가져올 것 (2026-09-08)

- **대상**: `~/Apps/itda-work/itda-django-bridge` — Django 개발을 Neovim/VS Code 안에서. 편집기는 UI 만 그리고 사실은 사이드카(`manage.py itda_bridge` / `python -m itda_django_bridge`)가 JSON 으로 공급한다. 이 저장소의 `examples/itda-django` 가 그쪽 테스트베드(읽기 전용)다.
- **결론 한 줄**: 제품은 다르지만 **"사실은 Python 한 곳, 얼굴은 여럿"** 이라는 구조가 우리의 "판정은 세계 한 곳, 문은 여럿" 과 같다. 가져올 것은 UI 가 아니라 **사이드카 계약·쿼리 캡처·Postgres 프로브** 셋이고, 전부 면 비교 트랙(`docs/인터페이스-비교-MCP-스킬HTTP-CLI.md`)의 실험 1 에 바로 쓰인다.

## 1. 차용 — 우선순위 순

| # | 가져올 것 | 어디에 | 왜 | 비용 |
|---|---|---|---|---|
| 1 | **사이드카 계약 = CLI 문의 계약** — `manage.py <명령> <sub> [args]` → `{"ok": true, "version": 1, "data": …}` / `{"ok": false, "error": {"kind", "message"}}`, 종료 코드 0/1, 서브커맨드 레지스트리(`@register`), `BridgeError(kind, message)` | 면 비교 실험 1 의 **C-1 `manage.py world <도구>`** (`django_itda/adapters/cli.py`) | 검증된 모양을 새로 정하지 않는다. `error.kind` 가 우리 갈래(`denied`·`forbidden`·`busy`·`exception`)와 정확히 대응한다 — `denied` 는 `rule_ids`·`alternatives` 를 `error` 안에 싣는다. 비교 축 "종료 코드 관례" 는 0(ALLOW·ESCALATE)/1(오류)/**2(DENY)** 로 실험 | 어댑터 ~80줄 |
| 2 | **`serve` 장수명 모드** — JSON lines + `id`, 기동 시 `ready` 이벤트, `stdout` 을 `stderr` 로 바꿔 스트림 보호, `stale`(import 된 프로젝트 소스 mtime 스냅샷 대조 → 응답 뒤 종료, 클라이언트가 재기동·재시도) | C-1 의 `world serve`; **`manage.py mcp_stdio` 에도 `stale` 감지** | (1) 실험 지표 "준비 비용·지연" 에서 `django.setup()` 매 호출(수백 ms) vs 장수명을 **같은 조건**으로 잰다 — MCP stdio 와 CLI serve 는 전송만 다른 쌍둥이가 된다. (2) 우리 `mcp_stdio` 는 소스가 바뀌면 낡은 답을 낸다 — 관찰 중 코드를 고치면 조용히 틀린다. `stale` 을 stderr 경고 + 종료로 | serve ~120줄(원문 거의 그대로), stale 은 `serve.py:_watched_files/snapshot/find_stale` 이식 |
| 3 | **`capture.collect`** — `connection.execute_wrapper` 로 블록 안의 SQL·ms·프로젝트 스택을 모은다(ORM·raw 불문). `normalize`(리터럴 → `?`)·`suspects`(N+1 의심) | `django_itda.trajectory` 에 **선택적 쿼리 캡처** → `ToolCall.query_count`·`sql_ms`; `manage.py race` 출력에 스레드별 SQL 순서 | 면 비교 지표 1 "세계 응답 동일성" 을 결과 JSON 이 아니라 **낸 쿼리**로 대조한다(문이 달라도 쿼리가 같아야 한다). 5·7단계 경합 실측이 "무엇이 먼저 UPDATE 를 잡았나" 를 눈으로 본다 | `capture.py` ~70줄 이식 + `ToolCall` 열 2개(마이그레이션 1) |
| 4 | **Postgres 프로브** — `probes/pg_settings.py`(테스트베드 settings 상속 + DB 만 교체), `just pg-up / pg-seed / probe-pg`(Docker 컨테이너, `uv run --with 'psycopg[binary]'`) | `examples/itda-django` 에 `just test-pg` | sol 7단계 리뷰가 지적한 백엔드 의존 셋을 **실측**한다 — (a) `ledger/0002` 트리거는 SQLite 문법(`RAISE(ABORT)`)이라 **Postgres 에서 migrate 가 깨진다**(`RunSQL` 에 vendor 가드 없음 — 지금 확인), (b) 5단계 "`select_for_update` 는 SQLite 에서 무시" 가 PG 에선 행 잠금이 된다, (c) `stock.deducted` 재조회의 READ COMMITTED 정확성. 셋 다 "보장하지 않는 것" 표의 8단계 행이었는데 프로브가 있으면 표가 실측으로 바뀐다 | 설정 1파일 + justfile 레시피 3개(원문 복사) + `0002` 에 `connection.vendor` 분기(PG 는 `CREATE FUNCTION … RAISE EXCEPTION` + trigger) |
| 5 | **`erd`(모델 → mermaid erDiagram)·`inspect`(settings diff·admin 등록)** | 단계별 지시서의 세계 ERD 자동 생성(`just erd` 가 `../itda-django-bridge` 를 `PYTHONPATH` 로 부른다 — 코드 복사 없음), 8단계 부채 리포트의 입력(admin 등록 모델 ↔ 권한 ↔ 도구 선언 대조) | 교재 준비 비용 절감. 8단계는 미진행이지만 부채 리포트는 면 비교에서 "스킬 문서가 권한을 모른다" 축의 실물 | 0(도구로 쓴다) |
| 6 | **결과 봉투의 `version`** — 계약을 바꾸면 `version` 을 올리고 History 에 남긴다는 규율 | `django_itda.results` 의 결과 모양에 `contract_version`(정수) | 우리 결과 모양은 이미 한 번 바뀌었고(v0.1 → v0.1.1 `handle` 필드) 클라이언트(스킬 문서·CLI 파서)가 그걸 알 길이 없다. HTTP·CLI 문이 생기면 파서가 둘 이상 | 필드 1개 + 문서 |
| 7 | **"문서가 정본이지만 실측이 이긴다 — 둘을 맞춘다"** + **"서브커맨드 하나 = 커밋 하나(Python + UI + 테스트 + 문서)"** | 루트 `CLAUDE.md` 검증 절 | 우리 규율(사양 §9·§10 정정)이 하던 일을 한 문장으로 | 0 |

## 2. 가져오지 않는 것

- Lua·VS Code 확장 — 다른 제품. 다만 **편집기가 승인 큐·장부를 보는 문**(사람 쪽 문)은 나중에 사이드카 하나로 얹을 수 있다는 점만 적어 둔다.
- `migrations`·`sql`·`schema`·`check`·`urls` 서브커맨드 — Django 자체 명령(`makemigrations --check`·`sqlmigrate`)으로 충분하고 우리 게이트가 이미 쓴다. 단 **학생 도구로는 권한다**: 7단계 원가계산(expand → backfill → contract)을 `:Dj migrations` 트리에서 `s`(DDL 미리보기)로 보면 `CheckConstraint` 가 실제로 무엇을 만드는지 보인다 — 지시서에 선택 항목으로 한 줄.
- `pytest_capture`(테스트별 쿼리 파일) — 3번을 이식하면 우리 테스트에서 직접 `collect()` 를 쓰는 편이 짧다.

## 3. 역방향 — 그쪽이 우리에게 기대는 것

브리지는 `examples/itda-django` 를 읽기 전용 테스트베드로 쓴다(`TESTBED` 변수 한 곳). 우리 단계 태그·마이그레이션(7단계의 4+1개, 트리거 `RunSQL`)이 그쪽 `migrations`·`sql` 서브커맨드의 실전 데이터다. **경로·이름을 바꾸면 그쪽 `justfile` 의 `TESTBED` 와 문서 링크를 같이 고친다**(구조 뒤집기 때 한 번 겪었다 — 브리지 커밋 `8b18b4f`).

## 4. 순서 제안

1. **면 비교 실험 1 의 C-1** 을 1·2번 계약으로 만든다(사양에 "사이드카 계약 v1 을 그대로 쓴다" 로 못 박고 `docs/설계.md` §5 와 대조).
2. 4번 Postgres 프로브를 먼저 붙여 **`ledger/0002` 트리거가 PG 에서 깨지는 것**을 실측하고 vendor 분기를 넣는다 — 7단계 미보장 표의 "PostgreSQL 대응" 행이 사실로 바뀐다. 사후 변경(태그 이동 없음).
3. 3번 쿼리 캡처를 `ToolCall` 에 붙이고 실험 1 의 지표 표에 "쿼리 동일성" 열을 더한다.
4. 5·6·7번은 그때그때.
