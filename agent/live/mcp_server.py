"""실접속(live) 트랙 — 진짜 Claude 를 세계에 붙이는 MCP 서버.

**이 프로세스는 Django 가 아니다.** stdio 로 도는 별도 프로세스이고, 하는 일은
`WORLD_URL` 의 HTTP API 를 두드려 응답을 그대로 옮기는 것뿐이다. 판정은 여기서
한 줄도 하지 않는다 — LLM 은 월드 서버의 클라이언트고, 이 파일이 그 사실의 물증이다.

Django 안에 MCP 를 호스팅하지 않는 이유는 8단계에서 다룬다(ASGI lifespan·세션·CSRF).

세계의 대답을 도구 결과로 옮기는 규칙은 셋이다.

    200  ALLOW     → 구조화 결과
    202  ESCALATE  → **오류가 아니다.** 승인 핸들이 담긴 결과 + `check_refund` 안내
    409  DENY      → 도구 오류. rule_ids·reason·alternatives 를 실어 보낸다
    403  권한 없음  → 도구 오류. "세계가 거부했다 — 권한 없음"

202 를 오류로 만들지 않는 것이 핵심이다. 격상은 실패가 아니라 **사람에게 올라간
상태**이고, 모델은 그 사실을 알고 기다릴 수 있어야 한다(MCP 2026-07-28 스펙의
Tasks 를 쓰지 않는 폴백 관행 — 승인 핸들 + 조회 도구).

409 를 오류로 만드는 것도 마찬가지로 의도다. 오류 메시지에 규칙 ID 와 대안이
들어 있어야 모델이 스스로 고칠 수 있다. "안 됩니다"만으로는 우회를 시도한다.

실행:
    WORLD_TOKEN=... uv run python agent/live/mcp_server.py
등록 방법은 `stages/live-실접속.md`.
"""

import os
import sys
from pathlib import Path

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

WORLD_URL = os.environ.get('WORLD_URL', 'http://127.0.0.1:8000').rstrip('/')
WORLD_TOKEN = os.environ.get('WORLD_TOKEN', '')

# AI 직원 시스템 프롬프트를 그대로 서버 instructions 로 싣는다.
#
# 여기 실린 것은 **부탁**이다. 부탁 중 어떤 문장이 실제로 코드에 이사됐는지는
# 프롬프트 자신이 표시해 두고 있고, 이사 표시가 없는 문장은 아무도 지켜 주지 않는다.
# 그 대조가 8단계 세계 부채 리포트의 입력이다 — 그래서 요약하지 않고 원문을 싣는다.
PROMPT_PATH = Path(__file__).resolve().parent.parent / 'prompts' / 'ai_staff.md'
INSTRUCTIONS = PROMPT_PATH.read_text(encoding='utf-8')

mcp = FastMCP('hyve-world', instructions=INSTRUCTIONS)


# --- 세계에 말 걸기 -----------------------------------------------------------


def _call(method, path, payload=None, *, report_only=False):
    """세계를 한 번 두드리고, 대답을 도구 결과 규약으로 옮긴다.

    `report_only` 는 **조회 도구용**이다. "지금 어떤 상태냐"고 물었는데 거부된
    건이라고 오류를 던지면, 그건 판정이 아니라 질문에 답을 안 한 것이다.
    조회는 무엇이 나오든 결과로 돌려준다.
    """
    if not WORLD_TOKEN:
        raise ToolError(
            '세계에 들어갈 열쇠가 없다 — 환경변수 WORLD_TOKEN 이 비어 있다. '
            'just token ai-staff 로 발급해 등록 설정에 넣어라.'
        )
    url = f'{WORLD_URL}{path}'
    headers = {'Authorization': f'Bearer {WORLD_TOKEN}'}
    try:
        response = httpx.request(method, url, json=payload, headers=headers, timeout=10.0)
    except httpx.RequestError as exc:
        raise ToolError(
            f'세계에 닿지 못했다 — {url} 에 연결할 수 없다({exc.__class__.__name__}). '
            f'다른 터미널에서 just run 이 돌고 있는지 확인하라.'
        ) from None

    try:
        body = response.json()
    except ValueError:
        raise ToolError(
            f'세계가 JSON 이 아닌 것을 돌려줬다 (HTTP {response.status_code}).'
        ) from None

    if report_only and response.status_code in (200, 202, 409):
        return _report(body)
    if response.status_code in (200, 201):
        return body
    if response.status_code == 202:
        return _escalated(body)
    if response.status_code == 403:
        raise ToolError(f'세계가 거부했다 — 권한 없음: {body.get("reason", "")}')
    if response.status_code == 401:
        raise ToolError(f'세계가 거부했다 — 인증 실패: {body.get("reason", "")}')
    if response.status_code == 409:
        raise ToolError(_denied(body))
    if response.status_code == 404:
        raise ToolError(f'세계에 그런 것이 없다: {body.get("reason", url)}')
    raise ToolError(f'세계가 HTTP {response.status_code} 로 답했다: {body}')


def _escalated(body):
    """202 — 사람에게 올라갔다. 오류가 아니라 **대기 상태**다."""
    refund = body.get('refund') or {}
    return {
        'decision': 'escalated',
        'refund_id': refund.get('id'),
        'status': refund.get('status', 'proposed'),
        'check_tool': 'check_refund',
        'message': '점주 승인 대기',
        'rule_ids': body.get('rule_ids', []),
        'reason': body.get('reason', ''),
        'alternatives': body.get('alternatives', []),
        'order': body.get('order'),
    }


DECISIONS = {'ALLOW': 'settled', 'ESCALATE': 'escalated', 'DENY': 'rejected'}


def _report(body):
    """조회 결과 — 판정을 한 단어로 요약해 앞에 붙여 준다."""
    return {'decision': DECISIONS.get(body.get('kind'), 'unknown'), **body}


def _denied(body):
    """409 — 규칙 ID 와 대안을 문장에 넣는다. 모델이 읽고 스스로 고칠 수 있게."""
    rules = ', '.join(body.get('rule_ids') or []) or '규칙 미상'
    alternatives = body.get('alternatives') or []
    text = f'세계가 거부했다 [{rules}] — {body.get("reason", "")}'
    if alternatives:
        text += ' / 대신 할 수 있는 것: ' + '; '.join(alternatives)
    return text


# --- 도구 --------------------------------------------------------------------


@mcp.tool
def list_orders() -> dict:
    """가게의 주문 목록을 읽는다.

    주의: 지금 이 세계에는 소유권 스코핑이 없다. 권한이 있으면 모든 고객의
    주문이 보인다(교육용으로 남겨 둔 구멍이다).
    """
    return _call('GET', '/api/orders/')


@mcp.tool
def get_order(order_id: int) -> dict:
    """주문 한 건의 상세 — 상품·금액·상태·걸려 있는 환불 제안."""
    return _call('GET', f'/api/orders/{order_id}/')


@mcp.tool
def place_order(items: list[dict] | None = None) -> dict:
    """주문을 접수한다. 결제 대기 상태의 주문이 하나 생긴다.

    `items` 는 `[{"product": "상품명", "quantity": 1}]`. 생략하면 고객 alice 의
    기본 장바구니로 접수한다. 주문자는 언제나 고객이다 — 대신 넣어 주는 것이다.
    """
    return _call('POST', '/api/orders/', {'items': items} if items else {})


@mcp.tool
def pay_order(order_id: int) -> dict:
    """결제 처리를 한다.

    결제 대기 상태의 주문만 결제 완료로 갈 수 있다. 이미 결제된 주문에 다시
    부르면 세계가 거부한다(ORDER-001@v1) — 권한 문제가 아니라 상태 문제다.
    """
    return _call('POST', f'/api/orders/{order_id}/pay/')


@mcp.tool
def propose_refund(order_id: int, amount: int | None = None, reason: str = '고객 요청') -> dict:
    """환불을 제안한다. `amount` 를 생략하면 전액.

    세 갈래로 답이 온다.
    - 규칙이 확정 — 기한 안이고 소액이면 바로 승인된다.
    - 점주 승인 대기 — 5만원을 넘으면 제안만 올라간다(`check_refund` 로 확인).
    - 거부 — 결제 후 7일이 지난 주문은 환불되지 않는다(REFUND-001@v1).
    """
    payload = {'order_id': order_id, 'reason': reason}
    if amount is not None:
        payload['amount'] = amount
    return _call('POST', '/api/refunds/', payload)


@mcp.tool
def check_refund(refund_id: int) -> dict:
    """환불 제안이 지금 어떤 상태인지 묻는다 — 승인 대기 건의 폴링용.

    알림은 오지 않는다. 점주가 admin 에서 누르면 여기 상태가 바뀔 뿐이다.
    `decision` 은 `escalated`(대기) / `settled`(확정됨) / `rejected`(거부됨).
    """
    return _call('GET', f'/api/refunds/{refund_id}/', report_only=True)


@mcp.tool
def approve_refund(refund_id: int) -> dict:
    """환불을 **확정**한다. 점주 권한(`orders.change_refund`)이 필요하다.

    AI 직원 계정으로는 거부된다. 제안까지가 AI 직원의 자리고, 확정은 점주의
    자리다 — 이 도구가 목록에 보인다는 것과 부를 수 있다는 것은 다른 얘기다.
    """
    return _call('POST', f'/api/refunds/{refund_id}/approve/')


if __name__ == '__main__':
    print(f'hyve-world MCP — WORLD_URL={WORLD_URL}', file=sys.stderr)
    mcp.run()
