# 변경 이력

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 를 따르고, 버전은 [SemVer](https://semver.org/lang/ko/) 를 따른다(0.x 동안 마이너가 호환 경계).

## [0.4.0] — 2026-09-19

호환성 릴리스. 공개 API·모델 필드·마이그레이션·결과 봉투 계약(`contract_version` 1)은 그대로다.

### 바뀐 것
- Django 의존 범위를 `>=5.2,<5.3` 에서 `>=5.2,<6.2` 로 넓혔다 — Django 6.0·6.1 지원(하한 5.2 LTS 유지).
- 분류자에 Python 3.12·3.13·3.14, Django 5.2·6.0·6.1 을 적었다.
- 워크스페이스 락(`uv.lock`)의 Django 를 6.1.1 로 올렸다.

### 더한 것
- GitHub Actions CI(`.github/workflows/ci.yml`) — Python 3.12·3.14 × Django 5.2·6.1, ruff · 패키지 테스트 · 예시 테스트.
- README 지원 버전 표, 이 변경 이력.

### 고친 것
- 없음 — Django 6.1.1·Python 3.14.7 에서 코드 수정 없이 전체 테스트가 통과했고 폐기 경고(`DeprecationWarning`·`PendingDeprecationWarning`)를 오류로 올려도 0건이었다(근거: `docs/reports/H-1.md`).

## 이전 (요약)

- **0.3.0** — 결과 봉투에 `contract_version`(1), 계약 History 규율.
- **0.2** — 호출 문맥(contextvar)·`CallContextMiddleware`; 도구 호출과 도메인 장부를 `call_id` 로 잇는다.
- **0.1 / 0.1.1** — 판정 계약·결과 매핑·`Toolset`·`ToolCall` 궤적·fastmcp 어댑터·`manage.py mcp_stdio`; 승인 핸들 필드.
