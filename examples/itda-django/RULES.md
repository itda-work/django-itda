# RULES.md — 규칙 대장

이 세계에서 시행 중인 법의 목록이다. **삭제 금지, 내용 보존, 상태만 제한적으로
갱신하는 버전 대장**이다 — 행을 지우지 않고 원문도 덮어쓰지 않는다.
규칙이 바뀌면 기존 행의 상태를 `deprecated`로 바꾸고 후속 버전을 새 행으로 덧붙인다.

| ID | 원문 | 출처 | 시행 형식 | 코드 위치 | 검증 테스트 | 상태 | 적용 경로 / 우회 가능 경로 |
|---|---|---|---|---|---|---|---|
| `REFUND-001@v1` | 결제 후 7일이 지난 주문은 환불하지 마라. | `agent/prompts/ai_staff.md` 1번 (점주, 2026-09-06) | 애플리케이션 검증(DENY 판정) | `orders/rules.py:REFUND_WINDOW_DAYS` · `orders/models.py:Refund.decide` | `tests/stage_04_transition_contract.py::test_결제_후_7일이_지난_환불은_DENY` | active | **적용**: `Refund.decide`/`Refund.apply` 경로만. **우회 가능**: admin·shell 에서 `Refund.objects.create()` 후 `approve()` 를 직접 부르면 판정을 통과하지 않는다. DB 제약이 아니다 |
| `REFUND-002@v1` | 5만원을 초과하는 환불은 반드시 점주 승인을 받아라. | `agent/prompts/ai_staff.md` 2번 (점주, 2026-09-06) | 격상(ESCALATE) + 권한(`orders.change_refund`) | `orders/rules.py:ESCALATE_OVER` · `orders/models.py:Refund.decide` | `tests/stage_04_transition_contract.py::test_기한_안_고액은_ESCALATE` | active | **적용**: 격상 판정은 `Refund.decide`, 권한 시행 위치는 **모델이 아니라** `orders/views.py:approve_refund` · `orders/admin.py:RefundAdmin.approve_selected`(`permissions=['change']`). **우회 가능**: 권한을 가진 계정이 shell 에서 `approve()` 직접 호출 |
| `ORDER-001@v1` | 이미 결제 완료된 주문을 다시 결제 완료로 만들지 마라. | `agent/prompts/ai_staff.md` 3번 (점주, 2026-09-06) | 전이 계약(조건부 UPDATE rowcount) | `orders/models.py:Order.mark_paid` | `tests/stage_04_transition_contract.py::test_pending_에서만_paid_로_간다` | active | **적용**: `mark_paid()` 경로의 계약. **우회 가능**: `Order.objects.filter(...).update(status='paid')` 는 이 계약을 지나지 않는다. **해석 주의** — 구현은 "pending 에서만"으로 원문("paid 재결제 금지")보다 **강하다**. 이 강화는 점주와의 합의로 둔 해석이다 |
| `REFUND-003@v1` | 같은 환불을 두 번 처리하지 마라. 한 주문에 환불은 한 번이다. | `agent/prompts/ai_staff.md` 4번 (점주, 2026-09-06) | **DB 제약**(부분 유일) + 전이 계약(조건부 UPDATE rowcount) + 멱등키 | `orders/models.py:Refund.Meta.constraints`(`refund_one_live_per_order`·`refund_idempotency_key_per_order`) · `Refund.approve`/`Refund.reject` · `Refund.apply` | `tests/stage_05_contention.py::test_살아_있는_환불은_주문당_하나다_DB제약` · `::test_동시_제안_두_개_중_하나만_확정된다` · `::test_확정은_제안_상태에서만_승인_뒤_거부` | active | 시행 면이 셋이고 보장 범위가 **면마다 다르다**. ① 살아 있는 환불은 주문당 하나 — **경로 무관**. shell·admin·raw SQL 어디서 `INSERT` 해도 걸린다(4단계 세 법과 층이 다른 첫 행이다). ② 확정은 제안 상태에서만 — `approve()`/`reject()` 경로의 계약. **우회 가능**: `Refund.objects.filter(...).update(status='rejected')` 는 이 계약을 지나지 않는다. ③ 멱등 재전송 — **`Refund.apply()` 로 행 생성에 성공한 열쇠만**. DENY 는 아무것도 저장하지 않고 ALREADY 응답도 저장하지 않으므로, 그 둘은 재생 대상이 아니다(재요청 시 현재 조건으로 새 판정한다). `Idempotency-Key` 없이 온 두 요청을 같은 요청이라고 부를 근거도 없다. **해석 주의** — DB 가 지키는 것은 **현재 행의 부분 유일성**이지 행 삭제·`update(status=...)` 직접 변경까지 막는 역사적 '평생 한 번'이 아니다 |
| `PAY-001@v1` | 결제는 네가 하지 마라. 고객에게 결제 링크를 안내하고 고객이 직접 결제하게 하라. 링크는 발급 후 1시간 안에만 유효하다. | `agent/prompts/ai_staff.md` 5번 (점주, 2026-09-07) | 발급 판정(ESCALATE) + **서명 토큰 만료**(프레임워크 법을 빌린다) + 전이 계약(4단계 그대로) | `orders/rules.py:PAY_LINK_TIMEOUT` · `orders/services.py:issue_payment_link` · `orders/tokens.py:PaymentLinkTokenGenerator` · `orders/views.py:pay_page` | `tests/stage_06_subject.py::test_한_시간_일_초_뒤_링크는_닫힌다` · `::test_정확히_한_시간은_유효하다` · `::test_결제된_주문의_링크는_스스로_닫힌다` | active | 시행 면이 셋이고 보장 범위가 면마다 다르다. ① **AI 가 결제를 완료시키지 못한다** — 권한이 아니라 **경로**다. `pay_order` 도구·API 는 `issue_payment_link` 만 부르고, `Order.mark_paid` 를 부르는 곳은 `orders/views.py:pay_page` 하나다. **우회 가능**: shell 에서 `order.mark_paid()` 직접 호출. ② **오래된 링크** — 토큰 타임스탬프 + `PAY_LINK_TIMEOUT`. 경계는 `>` 라 **정확히 1시간까지는 유효**하다. ③ **결제된 주문의 링크** — 해시에 `order.status` 를 섞어 상태가 바뀌면 같은 토큰이 스스로 죽는다. **해석 주의** — 만료는 **이중 결제를 막지 않는다**. 그건 `ORDER-001@v1` 의 몫이고, 두 겹이 순서대로 걸린다. 링크 발급·사용은 **아무 데도 안 남는다**(무상태 토큰의 대가 — 7단계) |
| `SCOPE-001@v1` | 고객의 주문은 그 고객에게만 보여 줘라. | `agent/prompts/ai_staff.md` 6번 (점주, 2026-09-07) | 쿼리 스코핑(애플리케이션 검증) | `orders/views.py:pay_page` (`get_object_or_404(Order, pk=pk, user=request.user)`) | `tests/stage_06_subject.py::test_남의_주문_결제_페이지는_404` · `::test_남의_주문은_결제도_못_한다` | active | **부분 이사다.** **적용**: 고객 문(결제 페이지) 하나. 남의 주문은 404 이고, 403 이 아닌 이유는 "있는데 못 본다"가 아니라 "네 세계에 없다"이기 때문이다. **미이사(우회 가능)**: AI 직원의 문 — `agent/live/tools.py:list_orders` · `orders/api.py:_order_list` · `get_order` 는 `orders.view_order` 권한 하나로 **모든 고객의 주문**을 돌려준다. admin·shell 도 마찬가지다. 프롬프트 6번의 이사 표시에도 `미이사` 를 적어 둔다 — 표시 없는 문장만 부채인 것이 아니라, **부분 이사의 남은 반쪽도 부채**다(8단계 리포트 첫 항목) |

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
- **5단계** — 네 번째 행이 생겼고, 이 행이 처음으로 **DB 제약**을 시행 형식으로 갖는다.
  4단계의 세 법은 전부 애플리케이션 경로 계약이라 "적용 경로" 열에 우회로가 적혀 있었는데,
  `refund_one_live_per_order` 는 우회로가 없다 — 3단계에서 `CHECK ("stock" >= 0)` 를
  보며 "전부 지켜지는 층"이라고 적어 둔 그 칸에 처음으로 **도메인 법**이 들어간 것이다.
  한 행에 시행 면이 셋인 이유는 경합의 모양이 셋이기 때문이고(INSERT·재전송·UPDATE),
  셋을 한 문장의 세 얼굴로 본다. 그래서 `REFUND-003@v2` 가 아니라 `@v1` 한 행이다.
- **6단계** — 두 행이 생겼고, 이 세계에 **사람이 둘**이 됐다. 확정하는 사람이
  점주 하나가 아니다 — 환불은 점주가, 결제는 **고객**이 확정한다. `PAY-001@v1` 은
  법을 새로 쓰지 않고 프레임워크의 것을 빌린다(`PasswordResetTokenGenerator`
  서브클래스): 비밀번호 재설정 링크가 3일 뒤 죽는 그 코드가 결제 링크를 1시간 뒤
  죽인다. `SCOPE-001@v1` 은 대장에 오른 **첫 부분 이사**다 — 고객 문에서만 지켜지고
  AI 직원 문에서는 아직이며, 그 한정이 "적용 경로 / 우회 가능 경로" 열에 적혀 있다.
  한 단계 = 법 하나 규율의 예외이고(4단계가 "단위 = 프롬프트 파일 하나"였듯 여기는
  **"단위 = 결제 페이지 하나"**다), 두 법은 같은 화면의 같은 요청에서 태어난다 —
  링크를 연 사람이 **누구**인가, **언제** 열었는가.
- **정책 확정(5단계)** — 승인 시점의 기한 재검사는 **하지 않는다.** `REFUND-001@v1` 의
  판정 시점은 **신청 시점**이고(4단계에서 정의했다), 5단계는 그것을 재검사 대상으로
  올리는 대신 정의로 종결한다. 기한은 사건의 성질이지 경합이 아니다. 바꾸려면 그건
  운영 판단이므로 `REFUND-001@v2` 라는 새 행이 필요하다 — 기존 행은 손대지 않는다.
