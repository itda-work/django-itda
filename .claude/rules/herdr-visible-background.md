# Herdr 안에서는 무음 장시간 작업을 sibling pane 에서 돌린다 — 진행 상황이 화면에 보여야 한다

세션이 Herdr 가 관리하는 pane 에서 기동됐으면(`HERDR_ENV=1`), **30초 이상 걸리면서 출력이 없는 작업**
(Codex 적대 리뷰 · 전수 테스트 · 빌드 · 프로브 재측정)은 `nohup`·`run_in_background` 로 숨기지 말고
**같은 탭의 sibling pane 에서 실행**한다. 사용자가 진행·차단·완료를 화면에서 직접 본다 (마스터 지시 2026-09-05).

## Why

`nohup codex exec … &` 로 띄운 리뷰는 끝날 때까지 아무것도 보이지 않는다. 사용자는 "멈췄나" 를 물어야 하고,
승인 대기(blocked)에 걸려도 아무도 모른다. Herdr 는 pane 단위로 프로세스와 에이전트 상태(`idle`·`working`·
`blocked`·`done`)를 인식하므로, pane 에 올리는 것만으로 관측면이 생긴다 — 그 상태를 에이전트도 `herdr agent wait`
로 읽으니 폴링 스크립트가 필요 없다.

## 강제 규칙

- **판별** — 매 세션 첫 장시간 작업 전 `test "${HERDR_ENV:-}" = 1`. 아니면 이 규칙은 비적용(종전 백그라운드 방식).
- **Codex 리뷰** — pane 에서 에이전트로 띄운다. 포커스는 호출 pane 에 둔다:

  ```bash
  P=$(herdr pane split --current --direction right --cwd "$PWD" --no-focus \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['result']['pane']['pane_id'])")
  herdr agent start reviewer --kind codex --pane "$P" -- --sandbox read-only
  herdr agent prompt reviewer "$(cat /tmp/review-prompt.md)" --wait --timeout 900000
  herdr agent read reviewer --source recent-unwrapped --lines 200     # 산출 회수
  ```

  `--wait` 가 `blocked` 로 돌아오면 `herdr agent read` 로 대기 UI 를 확인하고 사용자에게 묻는다(자동 응답 금지).
  산출이 대체 화면에 갇혀 `read` 로 회수되지 않으면 그때만 파일로 쓰게 요청한다(`--output-last-message` 를 처음부터
  주는 것도 유효 — 파일과 pane 둘 다 남는다).
- **명령형 장시간 작업**(테스트·빌드·프로브) — `herdr pane run "$P" "<명령>"` 뒤 `herdr pane wait-output "$P"
  --match "<종료 마커>" --timeout …` 으로 잡는다. 종료 마커는 성공·실패 양쪽을 덮는 정규식으로(`--regex
  'passed|failed|error'`) — 성공 문구만 기다리면 실패 시 무한 대기다(`gate-must-be-able-to-fail`).
- **분할 방향** — `herdr pane layout --pane "$HERDR_PANE_ID"` 로 넓으면 `right`, 좁거나 세로로 길면 `down`.
  같은 방향 연속 분할로 폭을 쪼개지 않는다. 작업이 끝난 pane 은 내가 만든 것만 닫는다.
- **하지 않는다** — 사용자 pane·남의 pane 을 대상으로 명령. `--current`·명시 pane ID·에이전트 이름만 쓴다.
  `herdr server stop`·메인 프로세스 종료 금지.
- **이미 `nohup` 으로 띄웠다면** — 옮길 수 없으니 최소한 `tail -f <로그>` 를 sibling pane 에 띄워 가시화한다
  (2026-09-05 실측: `wS:p4` 에 Codex 로그 tail).

## 적용 범위

Herdr 기동 세션의 Claude Code 작업 전반. Orca 등 다른 호스트에서 기동된 세션은 비적용. 도구별 상세 CLI 계약은
설치된 `herdr --help`·`herdr agent`·`herdr pane` 가 정본이다(버전마다 바뀐다 — 이 문서의 명령은 0.8.2 실측).

## History

- 2026-09-05: 초안 (마스터 지시 — "codex 리뷰 등 시각적으로 진행 상황이 보이지 않는 작업은 별도 herdr pane 으로").
  플러그인 재정비(#1648) 검수 Codex 리뷰를 `nohup` 으로 띄워 진행이 안 보였던 것이 계기. 같은 규칙을 hyve ·
  hyve-training · hyve-django 세 저장소에 동일 본문으로 둔다.
