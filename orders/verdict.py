"""판정 계약 — 세계가 요청에 답하는 형식.

판정은 참/거짓이 아니다. 세 값이다.

    ALLOW     규칙이 확정했다        200 / 201
    DENY      세계가 거부했다        409
    ESCALATE  사람에게 올린다        202

그리고 두 가지를 반드시 함께 실어 보낸다.

- `rule_ids` — 어떤 규칙이 그렇게 판정했는가. 대장(RULES.md)의 ID 다.
  "안 됩니다"가 아니라 "REFUND-001@v1 때문에 안 됩니다"여야 다툴 수 있다.
- `alternatives` — 그럼 어떻게 하면 되는가. 거절만 하고 길을 안 알려주면
  AI 직원은 우회를 시도한다(사람도 같다).

403 과 409 는 다른 말이다. 403 은 "너는 그럴 자격이 없다"(1단계),
409 는 "자격은 있지만 세계가 지금 그 상태가 아니다"(4단계)."""

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
