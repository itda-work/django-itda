"""결과는 한 모양인가 — 발견 1 의 회귀 시험.

임시 어댑터는 202 를 따로 조립하다가 `kind` 와 `outcome` 을 떨어뜨렸다.
"상태에 따라 키가 달라지지 않는다" 를 여기서 못 박는다.
"""

import django_itda
from django_itda.results import CONTRACT_VERSION, RESULT_KEYS, ToolBusy, ToolDenied, tool_result
from django_itda.verdict import Outcome, Verdict

ALLOWED = Verdict(kind=Verdict.ALLOW, reason='규칙이 확정했다')
ESCALATED = Verdict(
    kind=Verdict.ESCALATE,
    rule_ids=['REFUND-002@v1'],
    reason='5만원을 넘는다',
    alternatives=['점주 승인 큐에서 확정을 기다린다'],
)
DENIED = Verdict(
    kind=Verdict.DENY,
    rule_ids=['REFUND-001@v1'],
    reason='결제 후 7일이 지났다',
    alternatives=['주문 내역에서 환불 가능 기한을 확인한다'],
)


def test_세_판정이_같은_열쇠_집합을_낸다():
    results = [
        tool_result(ALLOWED, Outcome.COMMITTED, call_id='a'),
        tool_result(ESCALATED, Outcome.QUEUED, call_id='b', handle={'check_tool': 'check'}),
        tool_result(DENIED, Outcome.NOTHING, call_id='c'),
    ]

    assert [set(result) for result in results] == [set(RESULT_KEYS)] * 3


def test_모든_판정에_계약_버전이_실린다():
    results = [
        tool_result(ALLOWED, Outcome.COMMITTED, call_id='a'),
        tool_result(ESCALATED, Outcome.QUEUED, call_id='b', handle={'check_tool': 'check'}),
        tool_result(DENIED, Outcome.NOTHING, call_id='c'),
        tool_result(ESCALATED, Outcome.REPLAYED, call_id='d'),
    ]

    assert [result['contract_version'] for result in results] == [CONTRACT_VERSION] * 4
    assert CONTRACT_VERSION == 1 and isinstance(CONTRACT_VERSION, int)


def test_계약_버전은_패키지_최상위에서_읽힌다():
    assert django_itda.CONTRACT_VERSION == CONTRACT_VERSION
    assert django_itda.__version__ == '0.3.0'


def test_kind_와_outcome_은_1급이고_요약_열쇠는_없다():
    result = tool_result(ESCALATED, Outcome.REPLAYED, call_id='b')

    assert result['kind'] == Verdict.ESCALATE
    assert result['outcome'] == Outcome.REPLAYED
    assert 'decision' not in result, '한 단어로 접으면 판정과 결과 두 축이 한 축이 된다.'


def test_대상_객체는_그대로_실린다():
    result = tool_result(
        ALLOWED, Outcome.COMMITTED, call_id='a', order={'id': 2}, refund={'id': 1}
    )

    assert result['order'] == {'id': 2}
    assert result['refund'] == {'id': 1}


def test_ESCALATE_가_아니면_handle_은_None_이고_열쇠는_남는다():
    result = tool_result(ALLOWED, Outcome.COMMITTED, call_id='a')

    assert result['handle'] is None
    assert 'handle' in result, '키가 사라지면 읽는 쪽은 없는 것을 없다고 읽지 못한다.'


def test_판정_없이도_매핑된다_덕_타이핑():
    class 남의_판정:
        kind = 'ALLOW'
        rule_ids = []
        reason = '남의 세계도 이 모양으로 답한다'
        alternatives = []

        def as_dict(self):
            return {
                'kind': self.kind,
                'rule_ids': self.rule_ids,
                'reason': self.reason,
                'alternatives': self.alternatives,
            }

    result = tool_result(남의_판정(), Outcome.COMMITTED, call_id='a')

    assert result['kind'] == 'ALLOW'


# --- 오류 문장 ------------------------------------------------------------------


def test_거부_문장에_규칙_ID_와_대안이_실린다():
    text = str(ToolDenied(DENIED))

    assert text == (
        '세계가 거부했다 [REFUND-001@v1] — 결제 후 7일이 지났다 '
        '/ 대신 할 수 있는 것: 주문 내역에서 환불 가능 기한을 확인한다'
    )


def test_대안이_없으면_문장도_거기서_끝난다():
    거부 = Verdict(kind=Verdict.DENY, rule_ids=['ORDER-001@v1'], reason='상태 불일치')
    text = str(ToolDenied(거부))

    assert text == '세계가 거부했다 [ORDER-001@v1] — 상태 불일치'


def test_규칙_ID_가_없으면_규칙_미상():
    assert '[규칙 미상]' in str(ToolDenied(Verdict(kind=Verdict.DENY, reason='이유만 있다')))


def test_권한_거부는_판정이_아니다():
    denied = ToolDenied.forbidden('AI 직원은 제안할 수 있지만 확정할 수 없다')

    assert str(denied) == '세계가 거부했다 — 권한 없음: AI 직원은 제안할 수 있지만 확정할 수 없다'
    assert denied.verdict is None, '판정한 적이 없다 — 자격이 없어 판정에 닿지 못했다.'


def test_busy_는_판정_필드를_싣지_않는다():
    busy = ToolBusy()

    assert busy.retry_after == 1
    assert '거부(409)가 아니다' in str(busy)
    assert not hasattr(busy, 'verdict')
