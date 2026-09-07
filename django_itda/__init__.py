"""django-itda — Django 실행세계 도구면 패키지.

Django 서비스 함수가 돌려주는 `(판정, 결과)` 를 **한 모양의 도구 결과**로 옮기고,
그 호출을 **궤적**으로 남기고, 그 도구들을 `manage.py mcp_stdio` 로 MCP 클라이언트에
붙인다. **판정은 한 줄도 하지 않는다** — 무엇이 허용되는지는 언제나 사용자의
도메인 코드가 답한다.

    from django_itda.tools import Toolset

    toolset = Toolset(name='내-세계', instructions=프롬프트)

    @toolset.tool(perm='orders.add_refund', handle_tool='check_refund')
    def propose_refund(actor, order_id: int, amount: int | None = None):
        \"\"\"환불을 제안한다.\"\"\"
        verdict, outcome = services.propose_refund(actor, order, amount)
        return verdict, outcome.state, {'refund': refund_json(outcome.refund)}

전송·스키마 생성은 fastmcp 것을 쓴다(`django_itda.adapters.fastmcp`, 선택 의존).
첫 사용자는 교육 프로젝트 itda-django 이고, 그 실접속 관찰이 이 패키지의 사양 입력이다.
"""

__version__ = '0.1.1'
