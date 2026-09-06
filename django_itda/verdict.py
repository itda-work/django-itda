"""판정 계약 — 세계가 요청에 답하는 형식.

itda-django `orders/verdict.py` 의 것을 **일반화만** 해서 옮겼다. 문장을 다시
쓰지 않은 이유는 하나다 — 이 계약을 처음 세운 자리가 거기이고, 패키지는 그
자리를 옮긴 것이지 새로 정한 것이 아니다.

판정은 참/거짓이 아니다. 세 값이다.

    ALLOW     규칙이 확정했다        200 / 201
    DENY      세계가 거부했다        409
    ESCALATE  사람에게 올린다        202

그리고 두 가지를 반드시 함께 실어 보낸다.

- `rule_ids` — 어떤 규칙이 그렇게 판정했는가. 규칙 대장의 ID 다.
  "안 됩니다" 가 아니라 "REFUND-001@v1 때문에 안 됩니다" 여야 다툴 수 있다.
- `alternatives` — 그럼 어떻게 하면 되는가. 거절만 하고 길을 안 알려주면
  AI 직원은 우회를 시도한다(사람도 같다). **실제로 갈 수 있는 길만 적는다** —
  없는 길을 적으면 그건 대안이 아니라 거짓말이다. 빈 목록도 정직한 답이다.

403 과 409 는 다른 말이다. 403 은 "너는 그럴 자격이 없다",
409 는 "자격은 있지만 세계가 지금 그 상태가 아니다".

그리고 **판정과 결과는 다르다**. `ALLOW` 는 "허용된다" 는 판정일 뿐이고,
세계가 실제로 움직였는지는 `Outcome` 이 말한다.

**이 패키지는 판정을 한 줄도 하지 않는다.** 여기 있는 것은 판정을 담는 그릇이고,
판정은 사용자의 도메인 코드가 한다. 그래서 매핑이 요구하는 것은 이 클래스가
아니라 `VerdictLike` 프로토콜이다 — 이미 자기 `Verdict` 를 가진 프로젝트는
그것을 그대로 쓰면 된다(덕 타이핑).
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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

    @classmethod
    def from_dict(cls, data):
        """저장해 둔 판정을 되살린다 — 재전송에 **그때의 답**을 그대로 주기 위해.

        모르는 열쇠는 버린다. 판정의 모양이 나중에 바뀌어도 옛 행이 읽히지
        않는 일은 없어야 한다 — 장부는 과거를 계속 읽을 수 있어야 한다.
        """
        return cls(
            kind=data['kind'],
            rule_ids=list(data.get('rule_ids') or []),
            reason=data.get('reason', ''),
            alternatives=list(data.get('alternatives') or []),
        )


@dataclass(frozen=True)
class Outcome:
    """판정 다음에 세계에서 실제로 일어난 일.

        NOTHING    아무것도 쓰지 않았다 (DENY)
        QUEUED     승인 큐에 올렸다 — 대상은 아직 안 움직였다 (ESCALATE)
        COMMITTED  규칙이 확정했고 대상까지 옮겼다 (ALLOW)
        ALREADY    이미 처리된 건이 있어 그 상태를 그대로 돌려준다
        REPLAYED   같은 요청이 다시 왔다 — 그때의 답을 다시 준다

    `ALREADY` 와 `REPLAYED` 는 다른 사건이다. 섞으면 답이 거짓이 된다.

    - `ALREADY` — **다른 요청**인데 이미 처리된 건이 있다. 돌려주는 것은
      **지금 상태**다.
    - `REPLAYED` — **같은 요청**이 다시 왔다(같은 멱등키). 돌려주는 것은
      그 요청을 처음 받았을 때 저장해 둔 **그때의 판정**이다.

    itda-django 의 `refund` 필드는 여기서 `subject` 다 — 이 패키지는 환불을
    모른다. 결과가 실어 나르는 대상이 무엇인지는 도구가 정한다.
    """

    NOTHING = 'NOTHING'
    QUEUED = 'QUEUED'
    COMMITTED = 'COMMITTED'
    ALREADY = 'ALREADY'
    REPLAYED = 'REPLAYED'

    state: str
    subject: object = None

    @property
    def committed(self):
        return self.state == self.COMMITTED


@runtime_checkable
class VerdictLike(Protocol):
    """매핑이 요구하는 최소치 — 이것만 있으면 남의 `Verdict` 도 그대로 받는다.

    itda-django 의 `orders/verdict.py` 가 그대로 맞는다. 학생 코드를 이 패키지의
    임포트로 바꿔야 도구면을 얻는 구조였다면, 패키지가 교육의 순서를 침범한다.
    """

    kind: str
    rule_ids: list[str]
    reason: str
    alternatives: list[str]

    def as_dict(self) -> dict: ...
