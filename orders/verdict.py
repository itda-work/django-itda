"""판정 계약 — 세계가 요청에 답하는 형식.

판정은 참/거짓이 아니다. 세 값이다.

    ALLOW     규칙이 확정했다        200 / 201
    DENY      세계가 거부했다        409
    ESCALATE  사람에게 올린다        202

그리고 두 가지를 반드시 함께 실어 보낸다.

- `rule_ids` — 어떤 규칙이 그렇게 판정했는가. 대장(RULES.md)의 ID 다.
  "안 됩니다"가 아니라 "REFUND-001@v1 때문에 안 됩니다"여야 다툴 수 있다.
- `alternatives` — 그럼 어떻게 하면 되는가. 거절만 하고 길을 안 알려주면
  AI 직원은 우회를 시도한다(사람도 같다). **실제로 갈 수 있는 길만 적는다** —
  없는 길을 적으면 그건 대안이 아니라 거짓말이다. 빈 목록도 정직한 답이다.

403 과 409 는 다른 말이다. 403 은 "너는 그럴 자격이 없다"(1단계),
409 는 "자격은 있지만 세계가 지금 그 상태가 아니다"(4단계).

그리고 **판정과 결과는 다르다**. `ALLOW` 는 "허용된다"는 판정일 뿐이고,
세계가 실제로 움직였는지는 `Outcome` 이 말한다. 판정이 ALLOW 여도 커밋은
실패할 수 있고(그러면 아무것도 남지 않아야 한다), 이미 처리된 건이라면
새로 판정할 것이 아니라 **지금의 처리 상태**를 돌려줘야 한다."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Verdict:
    ALLOW = 'ALLOW'
    DENY = 'DENY'
    ESCALATE = 'ESCALATE'

    STATUS_CODE = {ALLOW: 200, DENY: 409, ESCALATE: 202}

    kind: str
    rule_ids: list[str] = field(default_factory=list)
    reason: str = ''
    alternatives: list[str] = field(default_factory=list)

    @property
    def status_code(self):
        return self.STATUS_CODE[self.kind]

    def as_dict(self):
        return {
            'kind': self.kind,
            'rule_ids': list(self.rule_ids),
            'reason': self.reason,
            'alternatives': list(self.alternatives),
        }


@dataclass(frozen=True)
class Outcome:
    """판정 다음에 세계에서 실제로 일어난 일.

        NOTHING    아무것도 쓰지 않았다 (DENY)
        QUEUED     승인 큐에 올렸다 — 주문은 아직 안 움직였다 (ESCALATE)
        COMMITTED  규칙이 확정했고 주문까지 옮겼다 (ALLOW)
        ALREADY    이미 처리된 건이 있어 그 상태를 그대로 돌려준다

    `ALREADY` 가 따로 있는 이유: 같은 요청이 두 번 와도 **다시 판정하는 것**과
    **지금 상태를 답하는 것**은 다른 일이다. 완전한 멱등 응답 저장은 5단계다 —
    여기서는 중복을 막지 않고, 다만 답을 정직하게 한다.
    """

    NOTHING = 'NOTHING'
    QUEUED = 'QUEUED'
    COMMITTED = 'COMMITTED'
    ALREADY = 'ALREADY'

    state: str
    refund: object = None

    @property
    def committed(self):
        return self.state == self.COMMITTED
