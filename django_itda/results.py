"""결과 매핑 — 도구가 세계에 무엇을 시켰든 **답의 모양은 하나**다.

이 모듈이 있는 이유는 관찰 하나다(`stages/live-실접속.md` 관찰 기록 2026-09-06
1차·2차, 발견 1). 임시 MCP 어댑터는 HTTP 코드마다 다른 조립기를 갖고 있었고,
202 를 조립하는 쪽에서 `kind` 와 `outcome` 을 통째로 떨어뜨렸다. 그 결과 같은
키로 재전송한 요청과 처음 요청이 모델에게 **완전히 같은 것**으로 보였다 —
세계는 `REPLAYED` 라고 말했는데 도구가 그 말을 지운 것이다.

그래서 답의 열쇠 집합을 고정한다.

    call_id · contract_version · kind · outcome · rule_ids · reason · alternatives · handle
    + 도구가 실은 대상 객체 (order · refund …)

`kind` 와 `outcome` 은 1급 필드이고 **항상 있다**. 상태에 따라 키가 생겼다
사라졌다 하면 읽는 쪽은 없는 것을 없다고 읽지 못한다.

`decision`(escalated/settled/rejected) 같은 요약 키는 **만들지 않는다**.
한 단어로 접어 주면 편해 보이지만, 접는 순간 `kind` 와 `outcome` 두 축이 한
축이 된다 — "격상됐다" 는 판정이고 "큐에 올라갔다" 는 결과이며, 둘은 따로 움직인다.

**계약 버전 규율** — `CONTRACT_VERSION` 은 이 모양의 버전이다. 결과 모양(최상위 키의
추가·삭제·의미 변경)을 바꾸면 이 수를 올리고 `docs/설계.md` §4 "계약 History" 에 남긴다.
**키 추가도 올린다** — 읽는 쪽이 "없는 것을 없다고" 읽을 수 있어야 한다. 버전이 그대로인데
키가 늘면, 옛 버전을 아는 쪽은 새 키가 없는 답을 "아직 안 실린 것" 인지 "없는 것" 인지
가르지 못한다. 두 번째 소비자(itda-hub)는 GitHub main 을 직접 소비하므로, 모양이 바뀌면
조용히 깨진다 — 이 수가 그 소음을 낸다.
"""

from .busy import BUSY_REASON, RETRY_AFTER

# 결과 모양의 버전(정수). 올리는 규율은 모듈 docstring 참고.
CONTRACT_VERSION = 1

# `handle` 은 상태와 무관하게 늘 있는 열쇠다. ESCALATE 가 아니면 `None` 이고,
# 그 `None` 이 "승인 대기가 아니다" 라는 답이다(키가 없는 것과는 다르다).
RESULT_KEYS = (
    'call_id',
    'contract_version',
    'kind',
    'outcome',
    'rule_ids',
    'reason',
    'alternatives',
    'handle',
)


def tool_result(verdict, outcome_state, *, call_id, handle=None, **objects):
    """판정 하나와 결과 하나를 도구 결과의 **그 한 모양**으로 옮긴다.

    `verdict` 는 `VerdictLike` 면 된다 — 이 패키지의 `Verdict` 일 필요가 없다.
    판정 필드는 펼쳐서 담는다. 클라이언트가 한 겹 덜 벗기고, 무엇보다
    모델이 오류 없이 규칙 ID 를 읽을 수 있다.
    """
    payload = verdict.as_dict()
    # 대상 객체를 **먼저** 펼치고 봉투 키를 나중에 얹는다 — 봉투가 언제나 이긴다.
    # 도구가 우연히 `kind`·`contract_version` 같은 이름의 키를 실어도 판정과
    # 계약 버전은 지워지지 않는다(발견 1 — 도구가 세계의 말을 지우면 안 된다).
    return {
        **objects,
        'call_id': call_id,
        'contract_version': CONTRACT_VERSION,
        'kind': payload['kind'],
        'outcome': outcome_state,
        'rule_ids': list(payload.get('rule_ids') or []),
        'reason': payload.get('reason', ''),
        'alternatives': list(payload.get('alternatives') or []),
        'handle': handle,
    }


class ToolDenied(Exception):
    """세계가 거부했다 — 도구 **오류**로 올린다.

    거부를 결과가 아니라 오류로 만드는 것은 의도다. 오류 문장에 규칙 ID 와
    대안이 들어 있어야 모델이 스스로 고칠 수 있다. "안 됩니다" 만으로는 우회를
    시도한다.

    두 가지 거부가 여기로 온다. 문장이 다르고, 그 차이가 정보다.

    - **판정 거부**(409) — `verdict` 를 들고 다닌다. 규칙 ID 와 대안이 실린다.
    - **권한 거부**(403) — `forbidden()`. `kind` 도 `rule_ids` 도 없다.
      판정한 적이 없기 때문이다. 자격이 없어 아예 판정에 닿지 못했다.
    """

    def __init__(self, verdict=None, message=None):
        self.verdict = verdict
        self._message = message
        super().__init__(self.__str__())

    def __str__(self):
        if self._message is not None:
            return self._message
        verdict = self.verdict
        rules = ', '.join(getattr(verdict, 'rule_ids', None) or []) or '규칙 미상'
        text = f'세계가 거부했다 [{rules}] — {getattr(verdict, "reason", "")}'
        alternatives = getattr(verdict, 'alternatives', None) or []
        if alternatives:
            text += ' / 대신 할 수 있는 것: ' + '; '.join(alternatives)
        return text

    @classmethod
    def forbidden(cls, reason):
        """자격이 없어 문 앞에서 멈췄다. 무엇이 안 되는지는 말해 준다.

        `reason` 을 굳이 문장으로 받는 이유 — 모델이 읽을 답이기 때문이다.
        "403" 만 던지면 LLM 은 우회를 시도한다.
        """
        return cls(message=f'세계가 거부했다 — 권한 없음: {reason}')


class ToolBusy(Exception):
    """잠금 실패 — 판정하지 못했다. 판정 필드를 싣지 않는다(판정한 적이 없다).

    문장은 itda-django 의 `BUSY_REASON` 그대로다. 부르는 쪽이 "이 요청은"
    으로 한정된 문장을 그대로 받아야 한다 — 다건 처리에서 앞 건이 이미
    커밋됐을 수 있는데 "아무 일도 안 일어났다" 로 읽히면 안 된다.
    """

    def __init__(self, reason=BUSY_REASON, retry_after=RETRY_AFTER):
        self.retry_after = retry_after
        super().__init__(reason)
