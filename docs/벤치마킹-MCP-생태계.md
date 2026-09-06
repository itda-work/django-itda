# 벤치마킹 — Django 위 "실행세계 도구면"을 위한 MCP 생태계 조사

- 조사일: 2026-09-06
- 목적: Django 위에 MCP 도구 노출 + ALLOW/DENY/ESCALATE 판정 응답 + 도구 호출 궤적 기록 + 권한 기반 도구 목록 필터링 패키지(django-itda)를 만들기 전, **감쌀 것과 새로 쓸 것**을 결정한다.
- 근거 우선순위: 공식 스펙·공식 SDK 소스/문서·PyPI/GitHub API 실측 → 그 외. 확인 못 한 항목은 **미확인**으로 표기.

---

## 0. 한 장 요약

1. **MCP 스펙은 2026-07-28 리비전에서 구조가 바뀌었다** — 세션(`Mcp-Session-Id`)·`initialize` 핸드셰이크 제거, 완전 무상태(stateless) 코어, 모든 결과에 `resultType` 필수, 서버→클라이언트 요청 대신 **MRTR(Multi Round-Trip Requests) — `InputRequiredResult`** 패턴, 장기 실행은 **Tasks 확장**(`io.modelcontextprotocol/tasks`)으로 분리. Roots·Sampling·Logging·DCR은 deprecated.
2. **공식 Python SDK `mcp` 는 v2(2026-07-28 스펙)로 넘어갔다** — v2.0.0 2026-07-28, 최신 v2.1.1 2026-08-25. `FastMCP` 클래스는 **삭제**되고 `MCPServer`(`mcp.server.mcpserver`)로 개명. `mcp.server.fastmcp` import 는 `ModuleNotFoundError` 를 의도적으로 던진다. v1.x(1.29.1, 2026-08-24)는 유지보수 모드.
3. **jlowin/fastmcp(현 PrefectHQ/fastmcp) 4.0** 이 2026-08-31 출시 — mcp v2 위에 구축, 2026-07-28 프로토콜 지원, 콜러블 기반 **authorization(`auth=` + `require_scopes`)** 로 tools/list 필터링과 호출 차단을 동시에 해결, `Middleware.on_call_tool` 로 궤적 기록 가능, `fastmcp-tasks` 로 Tasks 확장 서버 측 런타임 제공(Docket 기반).
4. **django-mcp-server(omarbenhamid → gts360) 는 mcp v1 전용이며 사실상 정지 상태** — 최신 PyPI 0.5.7(2025-10-10), 마지막 커밋 2026-03-10, 의존성 `mcp>=1.8.0` 상한 없음 → 신규 설치 시 mcp 2.x 가 깔려 `ImportError` 발생. v2 마이그레이션 PR #70(2026-08-02)이 한 달 넘게 미머지. 권한별 도구 목록 필터링 요청 이슈 #56(2025-11-20)은 답변 0.
5. **결론(권고 B)**: 공식 SDK v2 또는 fastmcp 4.x 를 **직접 감싼 독립 Django 패키지**를 만든다. django-mcp-server 위에 얹는 안(A)은 기반이 v1 에 묶여 있어 채택하지 않는다. 진짜 공백은 "Django `request.user`/permission 을 1급으로 두는 ALLOW/DENY/**ESCALATE** 판정 + Django ORM 에 남는 호출 궤적 + Tasks/MRTR 로 표현되는 사람 승인 대기"이며, 전송·OAuth RS·미들웨어·스키마 생성은 이미 있으므로 만들지 않는다.

---

## 1. 공식 `mcp` Python SDK (modelcontextprotocol/python-sdk)

| 항목 | 확인 내용 | 근거 |
|---|---|---|
| 최신 버전 | **2.1.1** (2026-08-25). 2.0.0 2026-07-28, 2.1.0 2026-08-24, 2.0.1 2026-08-26(백포트). v1 라인 1.29.1(2026-08-24) 유지보수 모드 | PyPI JSON `pypi.org/pypi/mcp/json` 실측; GitHub Releases |
| 라이선스 / Python | MIT / `>=3.10` | PyPI |
| 저장소 활동 | 24,208 stars, 마지막 push 2026-09-05, open issues 390 | GitHub API |
| 지원 스펙 | **2026-07-28** (v2). 연결별 프로토콜 협상으로 구형(핸드셰이크형) 클라이언트도 수용 | README, releases |
| `FastMCP` 클래스 | **v2 에서 삭제.** `mcp.server.mcpserver.MCPServer` 로 개명(`from mcp.server import MCPServer`). `src/mcp/server/fastmcp.py` 는 import 시 마이그레이션 안내가 담긴 `ModuleNotFoundError` 를 raise 하는 스텁만 남음 | `src/mcp/server/fastmcp.py`, `docs/migration.md` |
| 고수준 API | `@mcp.tool()` 데코레이터 유지, 타입힌트→스키마, `ctx: Context` 파라미터 주입(`get_context()` 삭제), 동기 핸들러는 워커 스레드에서 실행 | migration.md |
| 저수준 API | `mcp.server.lowlevel.Server` — 데코레이터 대신 생성자 `on_list_tools=`, `on_call_tool=` … 핸들러 주입. 핸들러 시그니처 `async (ctx: ServerRequestContext, params) -> ListToolsResult/CallToolResult` | `lowlevel/server.py`, migration.md |
| **미들웨어** | `MCPServer(middleware=[...])` 또는 `server.middleware.append(...)`. 시그니처 `async (ctx, call_next)`. `ctx.method`·`ctx.params`·`ctx.request_id` 관찰, `MCPError` raise 로 거부, `dataclasses.replace(ctx, params=...)` 로 재작성, `call_next` 미호출로 대체 응답 가능. 모든 인바운드 메시지를 감쌈. **provisional** — "2.x 마이너에서 시그니처 변경 가능" 명시 | `docs/advanced/middleware.md`, `mcpserver/server.py:275` |
| 전송 | stdio, Streamable HTTP(`streamable_http_app()`), SSE(`sse_app()`, 레거시). WebSocket 전송 삭제. 요청 본문 4MiB 제한(413) | migration.md, releases |
| ASGI 노출 | `app = mcp.streamable_http_app()` → Starlette `Mount`. **호스트 lifespan 에서 `mcp.session_manager.run()` 을 반드시 실행**해야 함(안 하면 첫 요청 RuntimeError). 여러 서버는 `AsyncExitStack`. `stateless_http=True`, `json_response=True` 옵션 | `docs/run/asgi.md`, `mcpserver/server.py:1279` |
| 인증 | MCP 서버 = OAuth 2.1 **Resource Server**. `MCPServer(auth=AuthSettings(issuer_url, resource_server_url, required_scopes), token_verifier=TokenVerifier)`. `TokenVerifier.verify_token(token) -> AccessToken \| None`. RFC 9728 `/.well-known/oauth-protected-resource` 자동 게시, 401 + `WWW-Authenticate`. 핸들러에서 `get_access_token()`(contextvar)으로 `client_id/scopes/subject/claims` 읽기. 내장 AS(`auth_server_provider=`)는 "AS/RS 분리 이전 설계, 신규 비권장". v2 에 RFC 9207 iss 검증, SEP-990 identity assertion, RFC 8693 token exchange | `docs/run/authorization.md`, `auth/middleware/auth_context.py`, releases |
| 스코프별 403 | 문서에 없음 — 핸들러가 `get_access_token().scopes` 를 직접 검사해야 함 | authorization.md |
| Elicitation / MRTR | `ctx.elicit()`(form/url). 2026-07-28 연결에서는 도구가 `InputRequiredResult(input_requests, request_state)` 를 반환하고 클라이언트가 `input_responses` + `request_state` 를 실어 **같은 도구를 재호출**. 선언형 의존성 `Elicit`, `Sample`, `ListRoots`. `request_state` 는 프로세스 로컬 키로 봉인 — 다중 인스턴스면 `keys=[...]` 필수 | `docs/handlers/multi-round-trip.md` |
| Tasks 확장 | v2.0.0 릴리스 노트: "Tasks Extension — 포함되지 않음, 이후 프리릴리스 예정". `docs/advanced/extensions.md` 에도 tasks 언급 없음(`io.modelcontextprotocol/ui` 만). **공식 SDK 에 Tasks 서버 런타임 없음(2026-09-06 기준)** | releases, extensions.md |
| 관측성 | OpenTelemetry 미들웨어 기본 내장(`OpenTelemetryMiddleware`), `opentelemetry-api>=1.28` 필수 의존 | lowlevel/server.py:440, migration.md |
| 의존성 변화 | `httpx`→`httpx2`, `mcp-types` 별도 배포(정확 핀), `pydantic>=2.12`, `anyio>=4.9` | migration.md |

**Django 관점 함의**: Django `ASGIHandler` 는 `scope["type"] != "http"` 를 거부한다(`django/core/handlers/asgi.py:167`, lifespan 미지원 — Trac #31508 오픈). 따라서 공식 SDK 의 `streamable_http_app()` 을 Django 안에 그대로 마운트할 수 없고, **Starlette 래퍼로 Django 와 MCP 앱을 나란히 마운트**하거나(lifespan 은 Starlette 가 받음), **Django 뷰가 요청 단위로 SDK 의 ASGI 핸들러를 구동**하는 브리지(django-mcp-server 방식)가 필요하다. 2026-07-28 스펙은 무상태이므로 후자 방식의 정당성이 오히려 커졌다.

## 2. jlowin/fastmcp → PrefectHQ/fastmcp (FastMCP 2.x/3.x/4.x)

| 항목 | 확인 내용 | 근거 |
|---|---|---|
| 저장소 | `jlowin/fastmcp` 는 **`PrefectHQ/fastmcp` 로 이전**(301). 27,535 stars, 마지막 push 2026-09-05, Apache-2.0 | GitHub API |
| 버전/빈도 | **4.0.3** (2026-09-05). 4.0.0 2026-08-31, 3.0.0 2026-02-18, 3.1.0 2026-03-03, 2.12.0 2025-08-31, 2.0.0 2025-04-11. 총 120 릴리스, 2~3주 간격 패치 | PyPI 실측 |
| 패키징 | `fastmcp` 는 메타패키지 → `fastmcp-slim[client,server]==4.0.3`. 서버 extra 가 `mcp>=2.0.0,<3.0.0`, `starlette>=1.0.1`, `authlib`, `joserfc`, `uvicorn` 등 끌어옴. extras: `tasks`(→`fastmcp-tasks`, `pydocket>=0.24.1`), `apps`, `code-mode`, `openai/anthropic/gemini` | PyPI requires_dist |
| 공식 SDK 와의 관계 | 4.x 는 mcp v2 위에 구축. 공식 SDK 가 주지 않는 것: 서버 합성(mount/import)·프록시·**OpenAPI→MCP 변환**(`FastMCP.from_openapi(spec, httpx_client)`, `RouteMap`/`MCPType`)·**auth 프로바이더 묶음**(JWTVerifier, StaticTokenVerifier, RemoteAuthProvider, OAuthProxy, OIDC Proxy, Full OAuth Server, MultiAuth; GitHub/Google/Azure/Auth0/Keycloak/WorkOS/Descope/Supabase… 통합 페이지)·**계층형 Middleware**·**Component Visibility**·**콜러블 Authorization**·Tool Transform/Tool Search/Code Mode·Tasks 런타임·CLI | gofastmcp.com llms.txt, 각 페이지 |
| 미들웨어(궤적 기록) | `class M(Middleware)` 의 `on_message / on_request / on_call_tool / on_list_tools / …` 훅. `context.message.name`, `context.message.arguments`, `result = await call_next(context)` 로 **호출 전·후·결과를 한 지점에서 기록 가능**. 내장: Logging/StructuredLogging/Timing/RateLimiting/ErrorHandling/Retry/ResponseCaching/Ping/ResponseLimiting. `on_list_tools` 에서 `Tool` 객체 리스트 필터링 가능(단 `on_call_tool` 에서도 차단해야 일관) | `servers/middleware.md` |
| **Authorization** (v3.0.0+) | `@mcp.tool(auth=[check])` — `check(ctx: AuthContext) -> bool`, `ctx.token: AccessToken \| None`, `ctx.component`. 헬퍼 `require_scopes(*scopes)`, `require_roles(*roles, extract=)`(4.0), `restrict_tag(tag, scopes=)`. **가시성(list 필터)과 강제(직접 호출 시 not-found)를 동시에 처리**. 서버 전역 `AuthMiddleware`, 4.0 에 `InsufficientScopeError(required_scopes=...)` | `servers/authorization.md` |
| Visibility (v3.0.0+) | `mcp.enable()/disable(names=, tags=, only=True)`, 세션 단위 `ctx.enable_components()/disable_components()` | `servers/visibility.md` |
| 인증/사용자 접근 | `get_access_token()` 의존성으로 `client_id/issuer/subject/scopes/claims`. `RemoteAuthProvider` 가 RFC 9728 메타데이터 게시; 단순 `TokenVerifier` 는 디스커버리 밖(사전 배포 토큰용) | `servers/auth/token-verification.md` |
| Elicitation | `ctx.elicit(message, response_type=bool)` 로 예/아니오 확인(핸드셰이크형 연결). **4.0/2026-07-28 무상태 연결에서는 "guard 패턴"** — `ctx.elicit` 대신 `InputRequiredResult` 반환, 재호출 시 `ctx.input_responses` 에서 답 읽음, 상태는 봉인된 `request_state` | `servers/elicitation.md` |
| Tasks | `@mcp.tool(task=True)` / `TaskConfig(mode="optional\|required\|forbidden")` + `TasksExtension()` 등록 + `fastmcp[tasks]`. 백엔드 Docket — `memory://`(단일 프로세스) 또는 `redis://`. 태스크 중 `InputRequiredResult` 로 **`input_required` 상태에서 사람 입력 대기** 가능. 4.0.0 신설, 2026-07-28 이상 연결 필요. PyPI 설명: "Tasks SEP 는 Final 이나 어떤 언어 SDK 도 런타임을 제공하지 않음 — fastmcp-tasks 가 첫 서버측 구현" | `servers/tasks.md`, PyPI fastmcp-tasks |
| ASGI 합성 | `mcp_app = mcp.http_app(path="/mcp")` → `Starlette(routes=[Mount(...)], lifespan=mcp_app.lifespan)`. **lifespan 전달 필수**(중첩 lifespan 불인식). `stateless_http=True`, `middleware=[...]`(Starlette 미들웨어), `@mcp.custom_route`. 문서는 Starlette/FastAPI 만 다룸 — **Django 언급 없음** | `deployment/http.md` |
| Django 와 함께 쓴 사례 | (a) **django-admin-fastmcp**(§4) — `fastmcp>=3.4,<4` 핀, **별도 프로세스**(`manage.py admin_mcp_serve`)로 실행, "lifespan 없는 ASGI 앱 안에 MCP 앱을 호스팅할 수 없다(session manager 가 시작되지 않음)" 명시. (b) 블로그 "Build a Hosted MCP Server in Django With FastMCP"(builtwithdjango.com, 게시일 미확인) — `asgi.py` 에서 Starlette 로 Django 앱과 `http_app()` 을 나란히 Mount, bearer → user 조회를 도구 안에서 수행, `close_old_connections()` 수동 호출. 공식 통합 문서 없음 | 각 README/블로그 |

## 3. django-mcp-server (omarbenhamid → gts360)

| 항목 | 확인 내용 | 근거 |
|---|---|---|
| 저장소 | `omarbenhamid/django-mcp-server` → **`gts360/django-mcp-server` 로 이전**(301). 376 stars, MIT, archived 아님 | GitHub API |
| 최신 릴리스 | PyPI **0.5.7 (2025-10-10)**. 총 12 릴리스(0.1.0 2025-05-10 ~). GitHub Releases 0건(태그 미사용). 저장소 `pyproject.toml` 의 version 은 0.5.6 (PyPI 와 불일치) | PyPI, GitHub API, pyproject |
| 유지 활동 | **마지막 커밋 2026-03-10**("Fix DRF Integration Body Processing #40"). 그 이전 2025-10-10. 오픈 이슈/PR 18개 — 2026-03~08 에 올라온 PR 8개(#60 커스텀 서버 클래스, #63 django-filter, #66 fields 경계, #67 icons, #68 pk 버그, **#70 mcp 2.x 마이그레이션**) 전부 미머지 | GitHub issues API |
| **mcp v2 비호환** | `views.py:7`, `djangomcp.py:16` 이 `from mcp.server import FastMCP` — v2 의 `mcp.server.__init__` 은 `MCPServer` 만 export 하므로 **ImportError**. 의존성 `mcp (>=1.8.0)` 상한 없음 → 2026-07-28 이후 신규 설치는 깨짐(PR #70 본문: "fresh installs pull an incompatible mcp 2.x"). 사용하려면 `mcp<2` 핀 필수 = 2025-06-18 스펙 세대에 고정 | 소스, PR #70 |
| WSGI/ASGI | **DRF `APIView`(`MCPServerStreamableHttpView`, csrf_exempt) 가 요청마다** `StreamableHTTPSessionManager(app=self._mcp_server, json_response=True, stateless=True)` 를 새로 만들어 `async_to_sync(_call_starlette_handler)` 로 SDK ASGI 핸들러를 구동. 그래서 WSGI 에서도 동작. 세션은 `Mcp-Session-Id` ↔ **Django SESSION_ENGINE** 키로 매핑(`stateless=True` 설정 시 생략). GET 스트림(서버→클라 알림) 미지원 | `djangomcp.py:164-230`, `views.py` |
| 인증 | `DJANGO_MCP_AUTHENTICATION_CLASSES` = DRF authentication 클래스 목록(기본 없음). 권장은 django-oauth-toolkit `OAuth2Authentication` + DCR. 보조 엔드포인트는 수동 설정 | README |
| 도구 선언 | `MCPToolset` 서브클래스(공개 메서드 = 도구, `self.request`/`self.context` 주입, `sync_to_async` 래핑), `ModelQueryToolset`(모델 질의 자동 노출, `get_queryset()` 오버라이드로 행 필터), `@mcp_server.tool()`(저수준 FastMCP, async ORM 필수), DRF 뷰 변환 `drf_publish_{create,update,delete,list}_mcp_tool` + `@drf_serialize_output`. `manage.py mcp_inspect` | README, 소스 |
| `request.user` | 클래스형 툴셋의 `self.request` 로 접근. DRF 뷰 변환 시 `_DRFRequestWrapper` 가 `request.user` 복제, DRF 뷰의 `permission_classes` 는 적용·`authentication_classes=[]` 로 비움 | `djangomcp.py:473-560` |
| 권한별 도구 목록 필터 | **없음.** 이슈 #56 "Listing Tools based on Permissions"(2025-11-20) 오픈, 댓글 0 | issue #56 |
| HITL·감사 | **없음.** 도구 호출 로그·승인 대기·ESCALATE 개념 없음. `admin.py` 63바이트, `models.py` 57바이트(실질 모델 없음) | 트리 크기 |
| stdio | `manage.py stdio_server` | README |
| 확장점 | 클래스 상속(`DjangoMCP(FastMCP)`)·`DJANGO_MCP_SERVER_CLASS`(PR #60, 미머지)·DRF auth 클래스 교체 정도. 훅/미들웨어 API 없음. FastMCP v1 의 `_tool_manager` 내부에 직접 접근 | 소스 |
| 라이선스 | MIT | PyPI |

**판정**: 설계는 유용(WSGI 브리지, Django 세션, DRF 재사용)하나 **기반 SDK 가 v1 에 고정되고 유지자가 2026-03 이후 부재**. 위에 얹으면 우리가 v2 마이그레이션까지 떠안게 된다.

## 4. 기타 Django MCP 시도

| 패키지 | 상태(한 줄) | 근거 |
|---|---|---|
| **kitespark/django-mcp** (`django-mcp` 0.3.1, 2025-05-30) | **아카이브됨**(GitHub archived=True, 마지막 push 2025-06-30). `mount_mcp_server` 로 Django ASGI 에 마운트하는 추상층. MIT | GitHub API, PyPI |
| **edelvalle/django-admin-fastmcp** (0.1.1, 2026-09-03) | 2026-08-19 생성, stars 0, 활발(push 2026-09-03). **Django admin 을 MCP 로 노출** — 고정 11개 범용 도구(`admin_list_models`…), 권한은 `ModelAdmin.has_*_permission/get_queryset` 에 전면 위임("병렬 권한 체계 없음"), 쓰기는 `ModelForm/save_model` 경유 후 **`LogEntry` 기록**("Via MCP (client: Claude Code)"), `delete_object/run_action` 은 `confirm=True` 없으면 미리보기만 반환. **자체 OAuth AS 를 Django 에 구현**(consent 페이지가 admin 세션 쿠키 사용, 토큰 해시 저장, 액세스 60분/리프레시 90일). `fastmcp>=3.4,<4` 핀(4.0 미대응), 별도 프로세스 `manage.py admin_mcp_serve`, stdio 없음. MIT | README, pyproject, GitHub API |
| **joshuadavidthomas/mcp-django** (`mcp-django` 0.14.0, 2026-07-23) | 목적이 다름 — **개발자용** Django 프로젝트 탐색 리소스 + 상태유지 shell 을 LLM 어시스턴트에 제공(도구 호스팅 프레임워크 아님). 활발(push 2026-08-26), 40 stars, MIT | PyPI, GitHub API |
| **vintasoftware/django-ai-assistant** (0.4.0, 2026-03-23) | OpenAI Assistants/LangGraph 기반 "AI 어시스턴트 in Django" 앱. PyPI 설명에 MCP 언급 없음 → **MCP 서버 노출 기능 미확인(없음으로 판단)**. 430 stars | PyPI, GitHub API |
| **mikeedjones/django-ninja-mcp** (0.0.1a2, 2025-04-18) | django-ninja 엔드포인트→MCP 도구 자동 변환(OpenAPI 활용, SSE 전송, `daphne`·`django-eventstream` 의존). 알파 2개 릴리스 후 PyPI 정지, 저장소 push 2026-04-13, 14 stars. `mcp>=1.6.0` — v2·Streamable HTTP 미대응 | PyPI, GitHub API |
| andychoi/django-mcp-inspector | Django 기반 MCP 서버용 인스펙터(검색 결과에서 확인, 내용 **미확인**) | 검색 |

## 5. MCP 스펙의 HITL/승인 장치 (2026-07-28 리비전 기준)

스펙 리비전 목록: 2024-11-05 → 2025-03-26 → 2025-06-18 → **2025-11-25** → **2026-07-28**(최신). `llms.txt` 실측.

| 장치 | 내용 | ESCALATE 표현 적합성 |
|---|---|---|
| **Tool annotations** | `readOnlyHint / destructiveHint / idempotentHint / openWorldHint / title`. 스펙: "클라이언트는 신뢰된 서버가 아니면 annotations 를 **신뢰하지 않아야(MUST)**". "사람이 루프에 있어 도구 호출을 거부할 수 있어야(SHOULD)" | **힌트일 뿐** — 클라이언트 측 확인 프롬프트 유도용. 서버가 승인을 강제하는 장치가 아님 |
| **Elicitation** (form/url) | 2026-07-28 에서 서버발 요청이 아니라 **MRTR** 로 동작: `tools/call` 응답이 `resultType: "input_required"` + `inputRequests{ "<key>": {method: "elicitation/create", params} }` + `requestState`. 클라이언트가 `inputResponses` + `requestState` 를 붙여 **새 request id 로 재호출**. 응답 액션 `accept / decline / cancel`. form 모드는 평면 원시타입 스키마만(bool 확인 가능). url 모드는 서버가 띄우는 웹페이지(승인 콘솔)로 유도 — 클라이언트는 URL 만 보고 결과는 재호출 시 서버가 판단. 서버는 요청을 **클라이언트·사용자 신원에 바인딩(MUST)**, url 모드는 열람자 신원 검증(MUST, 피싱 방지) | **"호출한 사용자 본인에게 되묻기"** 에 적합. 단, ESCALATE 가 **다른 사람(관리자)의 승인**이면 form 모드는 부적합(호출자가 스스로 승인하게 됨). url 모드로 Django 승인 페이지를 열고 `requestState` 에 승인 요청 ID 를 봉인 → 재호출 시 승인 상태 조회하는 흐름은 스펙 적합 |
| **Tasks 확장** (`io.modelcontextprotocol/tasks`, SEP-2663) | 코어에서 분리된 공식 확장. 서버가 `resultType: "task"` + `taskId/ttlMs/pollIntervalMs` 를 반환, 클라이언트가 `tasks/get` 폴링, 상태 `working / input_required / completed / failed / cancelled`. **`input_required` 에서 `inputRequests` 노출, 클라이언트가 `tasks/update` 로 응답**. `tasks/cancel` 협조적. `notifications/tasks` 는 `subscriptions/listen` 옵트인. 문서가 "**Human-in-the-loop 워크플로우 — 승인 게이트, 리뷰 단계**" 를 명시적 사용례로 든다. 클라이언트가 `_meta` 로 확장 선언한 경우에만 반환 가능(MUST) | **ESCALATE 의 표준 표현.** "승인 대기 중인 핸들"을 `taskId` 로 주고 승인자가 Django 쪽에서 처리하면 `working→completed` 로 전이. 제약: 클라이언트 지원 매트릭스 의존, **공식 Python SDK 에 서버 런타임 없음**(fastmcp-tasks 만 존재) |
| **폴백 관행** | 클라이언트가 Tasks 를 선언하지 않으면: (1) `isError: false` 의 일반 결과로 `{"decision":"escalated","approval_id":..., "check_tool":"..."}` 구조화 결과(`structuredContent` + `outputSchema`) 반환 후 별도 `get_approval_status` 도구로 폴링 — 스펙의 "Stateful Tools: 서버가 발급한 명시적 핸들을 도구 인자로 전달" 지침과 일치; (2) DENY 는 `isError: true` 실행 오류(모델이 자기수정 가능) 또는 권한 부족이면 HTTP 403 `insufficient_scope` + `scope=` 챌린지(step-up) | 표준 장치는 없으나 스펙 권고 패턴과 충돌하지 않음 |
| 진행 알림 | `notifications/progress` 는 해당 요청의 응답 스트림에서만 흐름(장기 대기에는 Tasks 가 대체) | 보조 |
| 기타 2026-07-28 변화 | `tools/list` 는 연결별로 달라지면 안 되지만 **"요청에 실린 인가(스코프)에 따라 달라질 수 있다(MAY)"** 고 명문화 → 권한별 도구 목록 필터링은 스펙 적합. 결정적 정렬 SHOULD, `ttlMs/cacheScope` 필수(`cacheScope: "private"` 로 사용자별 목록 캐시 격리). `_meta` 에 OpenTelemetry `traceparent` 전파 규약 — 궤적 기록과 연계 가능 | — |

## 6. django-ninja 와의 비교 참조

- django-ninja 1.7.0(2026-08-30), 9,182 stars, 활발. Pydantic `Schema` + 함수 시그니처에서 OpenAPI 생성.
- **재사용 가능성**: ninja 의 "뷰 시그니처(타입힌트+Pydantic)→JSON Schema" 파이프라인은 MCP `inputSchema/outputSchema` 생성과 동형이다. 두 경로가 있다.
  1. **OpenAPI 경유**: `api.get_openapi_schema()` → `FastMCP.from_openapi(spec, httpx.AsyncClient(base_url, headers))`. HTTP 자기호출(프록시)이라 `request.user` 가 토큰 재전달로만 이어짐 — fastmcp 문서 자체가 "자동 변환보다 손으로 만든 서버가 LLM 성능이 훨씬 좋다" 고 경고.
  2. **직접 변환**: ninja `Operation` 의 `view_func`, `models`(Pydantic 파라미터 모델), `response_models` 를 읽어 in-process 로 MCP 도구 등록. `django-ninja-mcp` 가 이 방향을 시도했으나 알파에서 정지(v1 SDK·SSE).
- 유지자 입장: vitalik 이 이슈 #1449(2025-10-14 댓글)에서 "MCP 도구는 REST 와 대개 완전히 다르다 — 별도 패키지에서 하라" 고 명시 → **ninja 본체에 MCP 가 들어올 계획 없음**.
- 시사점: "뷰 선언에서 도구 생성" 은 DRF(django-mcp-server 가 이미 함)·ninja 모두 기술적으로 가능하지만, 도구 설계 단위가 REST 자원과 다르다는 유지자 의견과 fastmcp 경고가 일치한다. **옵션 어댑터로 두고 핵심은 명시적 도구 선언**이 맞다.

---

## 7. 결론

### 7.1 기능 × 후보 표

범례: ● 있음 / ◐ 부분 / ○ 없음. "우리 몫" = 어느 후보를 택해도 django-itda 가 써야 하는 부분.

| 요구 기능 | 공식 SDK v2 직접 | fastmcp 4.x | django-mcp-server 0.5.7 |
|---|---|---|---|
| 전송: Streamable HTTP(무상태) | ● `streamable_http_app(stateless_http=True)` | ● `http_app(stateless_http=True)` | ◐ v1 SDK 로 구동(2025-06-18 세대), WSGI 동작이 강점 |
| 전송: stdio `manage.py` 명령 | ◐ `run(transport="stdio")` 는 있음, 명령은 우리 몫 | ◐ 동일 | ● `stdio_server` |
| Django ASGI 안에 마운트 | ○ lifespan 필요 → Starlette 래퍼 또는 뷰 브리지(우리 몫) | ○ 동일("Django 미언급") | ● DRF 뷰 브리지(요청당 세션매니저) |
| 인증: OAuth 2.1 RS(RFC 9728 메타·401 챌린지) | ● `AuthSettings`+`TokenVerifier` | ● + 프로바이더 다수·OAuthProxy·MultiAuth | ◐ DRF auth 클래스 위임(oauth-toolkit 권장), RFC 9728 메타 게시 **미확인** |
| Django 세션/쿠키 인증 | ○ | ○ | ◐ Django 세션을 MCP 세션 키로 사용(로그인 세션 재사용은 아님) |
| `request.user` 주입 | ◐ `get_access_token()` 까지; 토큰→User 매핑은 우리 몫 | ◐ 동일 | ● `self.request.user` |
| 권한별 도구 목록 필터 | ◐ 미들웨어로 `tools/list` 결과 재작성(provisional API) | ● `auth=[...]`·`require_scopes`·세션 visibility — list 와 call 동시 적용 | ○ (이슈 #56 미답) |
| ESCALATE(사람 승인 대기) 응답 | ◐ MRTR `InputRequiredResult`(호출자 본인). Tasks 런타임 ○ | ● `fastmcp-tasks`(`input_required`) + guard 패턴 MRTR | ○ |
| 도구 호출 궤적 미들웨어 | ● `middleware=[...]`(provisional) + OTel 내장 | ● `Middleware.on_call_tool`(안정 API, 3.x 부터) | ○ |
| 궤적의 Django ORM 영속화 | ○ 우리 몫 | ○ 우리 몫 | ○ |
| DRF 자동 노출 | ○ | ○ | ● `drf_publish_*` |
| ninja 자동 노출 | ○ | ◐ OpenAPI 프록시(`from_openapi`) | ○ |
| ORM 자동 노출 | ○ | ○ | ● `ModelQueryToolset` |
| Tool annotations(`destructiveHint` 등) | ● | ● | ◐ v1 API 통해 가능, 문서 없음 |
| 2026-07-28 스펙 | ● | ● | ○ (`mcp<2` 필요) |
| 유지 상태 | 공식, push 2026-09-05 | push 2026-09-05, 2~3주 릴리스 | 마지막 커밋 2026-03-10, v2 PR 미머지 |
| 라이선스 | MIT | Apache-2.0 | MIT |

### 7.2 권고 A / B

**A. django-mcp-server 위에 확장 패키지**

- 장점: WSGI 브리지·Django 세션·DRF 재사용·`self.request.user` 가 이미 있다. 표면 API 가 Django 답다.
- 단점: 기반이 **mcp v1 전용**(2025-06-18 세대) — 신규 설치가 깨져 `mcp<2` 핀을 강제해야 하고, MRTR·Tasks·`resultType`·무상태 코어 등 ESCALATE 표현에 필요한 2026-07-28 장치를 전부 쓸 수 없다. 훅/미들웨어 API 가 없어 궤적 기록·목록 필터를 위해 `_tool_manager` 등 **private 내부에 몽키패치**해야 한다. 유지자 부재(마지막 커밋 6개월 전, v2 PR 한 달+ 방치)로 포크가 사실상 불가피 → 우리가 v2 마이그레이션까지 떠안는다.
- 유지 리스크: **높음**. 라이선스: MIT — 포크·재배포 문제 없음.

**B. 공식 SDK v2 / fastmcp 4.x 를 직접 감싼 독립 패키지** — **권고**

- 장점: 2026-07-28 스펙 장치를 즉시 사용. 궤적 기록·권한 필터·ESCALATE 를 SDK 공식 확장점(미들웨어·`auth=`·Tasks·MRTR)에 얹는다. Django 고유 부분(뷰 브리지, User 매핑, ORM 영속, 승인 UI, `manage.py` 명령)만 우리가 쓴다.
- 단점: WSGI 브리지·stdio 명령·`request.user` 주입은 새로 쓴다(django-mcp-server 의 `handle_django_request` 설계를 **참고**하면 규모는 작다 — 핵심 60줄). fastmcp 채택 시 의존성이 크다(`starlette`, `authlib`, `uvicorn`, `pydocket`(tasks) 등).
- 두 갈래 중 선택:
  - **B-1 fastmcp 4.x 기반(권고)**: 권한 필터(`auth=`)·궤적(`on_call_tool`)·ESCALATE(`fastmcp-tasks`) 가 **안정 공개 API** 로 이미 있다. 공식 SDK 의 미들웨어는 provisional 이라 우리가 그 위에 쌓으면 마이너 릴리스에 깨질 수 있다. 리스크: fastmcp 는 메이저 판올림이 잦다(2.0 → 3.0 10개월, 3.0 → 4.0 6개월). 완화: django-itda 가 fastmcp 를 **어댑터 한 모듈로 격리**(`django_itda.adapters.fastmcp`)하고 도구 선언·판정·궤적 모델은 fastmcp 타입에 의존하지 않게 설계.
  - **B-2 공식 SDK 만**: 의존성 최소·공식성. 대가로 Tasks 런타임과 authorization 헬퍼를 직접 구현해야 하고 미들웨어 API 가 provisional. Tasks 없이 MRTR + 폴링 도구 폴백만으로 시작할 때 유효.
- 라이선스 호환: MIT(mcp) / Apache-2.0(fastmcp) 모두 MIT 배포 패키지가 의존·감싸기 가능. fastmcp 코드를 **복사**해 넣을 경우에만 NOTICE 유지 의무.

### 7.3 진짜 공백 (아무도 안 하는 것 → django-itda 가 만들 것)

1. **Django 권한 체계(`User`·`Permission`·Group·객체 권한)를 1급 입력으로 받는 ALLOW/DENY/ESCALATE 3값 판정기** — 생태계는 scope 기반 2값(허용/불가)만 있다. django-admin-fastmcp 가 `ModelAdmin` 권한 위임을 보여주지만 admin 한정·2값·`confirm=True` 자기확인.
2. **ESCALATE 의 표준 표현 결합**: 판정 결과 "승인 필요" → Tasks 확장(`input_required`) 또는 MRTR url 모드 elicitation → Django 승인 화면(관리자 = 호출자와 다른 사람) → 승인 시 태스크 완료. 이 결합을 구현한 Django 패키지 없음. 클라이언트가 Tasks 미지원일 때의 **폴백(승인 핸들 구조화 결과 + 조회 도구)** 도 없음.
3. **도구 호출 궤적의 Django ORM 영속 모델 + admin** — `LoggingMiddleware` 는 stdout, django-admin-fastmcp 는 admin `LogEntry` 재사용(쓰기만). 호출자·도구·인자·판정·결과·소요·`traceparent` 를 한 행으로 남기고 admin 에서 조회하는 패키지 없음.
4. **Django 뷰 브리지의 v2 재구현** — 2026-07-28 무상태 코어와 맞는 "요청당 SDK 핸들러 구동"(WSGI 겸용) 브리지. django-mcp-server 가 유일했으나 v1 정지.
5. **Django 로그인 세션(쿠키) 기반 인증 어댑터** — 사내 브라우저형 MCP 클라이언트·개발용. 모든 후보가 Bearer 전제.
6. **`manage.py` 통합**: `itda_mcp_stdio`(stdio, Claude Desktop/Code 로컬), `itda_mcp_inspect`(도구·판정 표), `itda_mcp_serve`(lifespan 있는 Starlette 래퍼 실행).

### 7.4 이미 있어서 만들지 말아야 할 것

| 만들지 말 것 | 대신 쓸 것 |
|---|---|
| JSON-RPC/Streamable HTTP 전송, `resultType`·`_meta` 처리, 프로토콜 버전 협상 | mcp v2 / fastmcp 4 |
| OAuth 2.1 Resource Server(RFC 9728 메타, 401/403 챌린지, 토큰 검증) | `AuthSettings`+`TokenVerifier` 또는 fastmcp `RemoteAuthProvider/JWTVerifier` (자체 AS 가 필요하면 django-oauth-toolkit) |
| 스코프 기반 도구 가시성·강제 | fastmcp `auth=[require_scopes(...)]` — 우리는 그 위에 Django 권한 콜러블만 제공 |
| 타입힌트/Pydantic → `inputSchema/outputSchema` | 두 SDK 내장 |
| 도구 호출 가로채기 파이프라인 | fastmcp `Middleware.on_call_tool` / mcp `middleware=` |
| 장기 실행·승인 대기 태스크 상태 머신·폴링 | `fastmcp-tasks`(Docket) — 자체 큐 구현 금지 |
| OpenTelemetry 계측 | mcp v2 내장 `OpenTelemetryMiddleware` |
| OpenAPI→도구 자동 변환(ninja 포함) | `FastMCP.from_openapi` |
| Tool annotations 스키마 | 스펙/SDK 타입 그대로 |
| 폼 elicitation 스키마·액션 처리 | SDK `Elicit`/`ctx.elicit` |

### 7.5 미확인 항목

- fastmcp 4.x 미들웨어가 2026-07-28 무상태 연결에서 `on_initialize` 대신 `on_discover` 만 타는지(문서에 `on_discover` 훅 존재만 확인).
- django-mcp-server 가 RFC 9728 메타데이터 엔드포인트를 게시하는지(README 에서 못 찾음).
- `fastmcp-tasks` 의 `memory://` 백엔드가 Django `runserver`(스레드) 환경에서 동작하는지 — 문서상 "단일 프로세스 한정".
- 공식 SDK 의 Tasks 프리릴리스 일정(릴리스 노트 "later pre-release" 이후 갱신 없음).
- Tasks 확장 클라이언트 지원 매트릭스(Claude Desktop/Code 등) — 스펙 사이트 `extensions/client-matrix` 미열람.
- builtwithdjango 블로그 게시일·fastmcp 버전.

---

## 부록 — 근거 URL

- 스펙: https://modelcontextprotocol.io/specification/2026-07-28/changelog · /deprecated · /server/tools · /client/elicitation · /basic/authorization · https://modelcontextprotocol.io/extensions/tasks/overview · https://github.com/modelcontextprotocol/ext-tasks
- 공식 SDK: https://github.com/modelcontextprotocol/python-sdk (releases; `docs/migration.md`, `docs/advanced/middleware.md`, `docs/run/asgi.md`, `docs/run/authorization.md`, `docs/handlers/multi-round-trip.md`, `docs/advanced/extensions.md`; `src/mcp/server/fastmcp.py`, `src/mcp/server/mcpserver/server.py`) · https://pypi.org/project/mcp/
- fastmcp: https://github.com/PrefectHQ/fastmcp · https://gofastmcp.com/changelog · /servers/middleware · /servers/authorization · /servers/visibility · /servers/tasks · /servers/elicitation · /servers/auth/token-verification · /deployment/http · /integrations/openapi · https://pypi.org/project/fastmcp/ · https://pypi.org/project/fastmcp-tasks/
- django-mcp-server: https://github.com/gts360/django-mcp-server (PR #70, issue #56; `mcp_server/djangomcp.py`, `mcp_server/views.py`) · https://pypi.org/project/django-mcp-server/
- 기타: https://github.com/kitespark/django-mcp · https://github.com/edelvalle/django-admin-fastmcp · https://github.com/joshuadavidthomas/mcp-django · https://github.com/vintasoftware/django-ai-assistant · https://github.com/mikeedjones/django-ninja-mcp · https://github.com/vitalik/django-ninja/issues/1449 · https://builtwithdjango.com/blog/build-a-hosted-mcp-server-in-django-with-fastmcp
- Django: `django/core/handlers/asgi.py`(lifespan 거부) · https://code.djangoproject.com/ticket/31508
