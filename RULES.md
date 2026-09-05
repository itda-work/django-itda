# RULES.md — 규칙 대장

이 세계에서 시행 중인 법의 목록이다. **삭제 금지, 내용 보존, 상태만 제한적으로
갱신하는 버전 대장**이다 — 행을 지우지 않고 원문도 덮어쓰지 않는다.
규칙이 바뀌면 기존 행의 상태를 `deprecated`로 바꾸고 후속 버전을 새 행으로 덧붙인다.

| ID | 원문 | 출처 | 시행 형식 | 코드 위치 | 검증 테스트 | 상태 | 적용 경로 / 우회 가능 경로 |
|---|---|---|---|---|---|---|---|
| `REFUND-001@v1` | 결제 후 7일이 지난 주문은 환불하지 마라. | `agent/prompts/ai_staff.md` 1번 (점주, 2026-09-06) | 애플리케이션 검증(DENY 판정) | `orders/rules.py:REFUND_WINDOW_DAYS` · `orders/models.py:Refund.decide` | `tests/stage_04_transition_contract.py::test_결제_후_7일이_지난_환불은_DENY` | active | **적용**: `Refund.decide`/`Refund.apply` 경로만. **우회 가능**: admin·shell 에서 `Refund.objects.create()` 후 `approve()` 를 직접 부르면 판정을 통과하지 않는다. DB 제약이 아니다 |
| `REFUND-002@v1` | 5만원을 초과하는 환불은 반드시 점주 승인을 받아라. | `agent/prompts/ai_staff.md` 2번 (점주, 2026-09-06) | 격상(ESCALATE) + 권한(`orders.change_refund`) | `orders/rules.py:ESCALATE_OVER` · `orders/models.py:Refund.decide` | `tests/stage_04_transition_contract.py::test_기한_안_고액은_ESCALATE` | active | **적용**: 격상 판정은 `Refund.decide`, 권한 시행 위치는 **모델이 아니라** `orders/views.py:approve_refund` · `orders/admin.py:RefundAdmin.approve_selected`(`permissions=['change']`). **우회 가능**: 권한을 가진 계정이 shell 에서 `approve()` 직접 호출 |
| `ORDER-001@v1` | 이미 결제 완료된 주문을 다시 결제 완료로 만들지 마라. | `agent/prompts/ai_staff.md` 3번 (점주, 2026-09-06) | 전이 계약(조건부 UPDATE rowcount) | `orders/models.py:Order.mark_paid` | `tests/stage_04_transition_contract.py::test_pending_에서만_paid_로_간다` | active | **적용**: `mark_paid()` 경로의 계약. **우회 가능**: `Order.objects.filter(...).update(status='paid')` 는 이 계약을 지나지 않는다. **해석 주의** — 구현은 "pending 에서만"으로 원문("paid 재결제 금지")보다 **강하다**. 이 강화는 점주와의 합의로 둔 해석이다 |

## 열 정의

- **ID** — `<도메인>-<번호>@v<버전>` 형식. 예: `REFUND-001@v1`. 번호는 재사용하지 않는다.
- **원문** — 사람이 말한 그대로의 규칙 문장. 코드 요약이 아니라 요구사항 원문을 적는다.
- **출처** — 누가·어디서 이 규칙을 말했는가(회의록·프롬프트 파일·정책 문서).
- **시행 형식** — 무엇이 이 규칙을 지키는가. `DB 제약` / `전이 계약` / `권한` / `애플리케이션 검증` / `프롬프트(시행 아님)`.
- **코드 위치** — `파일:심볼` 형태. 시행 형식이 `프롬프트(시행 아님)`이면 그 프롬프트 파일 경로.
- **검증 테스트** — 이 규칙이 살아 있음을 증명하는 테스트. `tests/stage_NN_*.py::함수명`.
- **상태** — `active` / `deprecated`(후속 버전 ID를 함께 적는다).
- **적용 경로 / 우회 가능 경로** — 이 규칙이 **어디를 지나야** 지켜지는가, 그리고
  어디로 가면 지나지 않는가. 애플리케이션 검증은 DB 제약과 다르다 — 경로를 벗어난
  호출(shell·admin 직접 조작)까지 막아 주지는 않는다. 원문보다 강한 구현이라면
  그것이 **해석**임도 여기 적는다.

## 현황

- **0~3단계** — 행이 없었다. 그 세계에는 학생이 선언한 도메인 법이 하나도 없었다
  (권한·세션·`PositiveIntegerField` 는 Django 가 딸려 준 범용 법이라 대장에 오르지 않는다).
- **4단계** — 세 행이 생겼다. `agent/prompts/ai_staff.md` 의 자연어 세 문장이
  시행 코드로 이사한 결과다. 프롬프트의 원문은 **지우지 않고** 이사 표시만 달았다 —
  금지문과 시행 코드의 대조가 8단계 세계 부채 리포트의 입력이다.
