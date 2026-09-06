"""`mcp_stdio` 시험이 가리킬 더미 세계 — 설정 문자열이 실제로 풀리는지 본다."""

from django_itda.tools import Toolset
from django_itda.verdict import Outcome, Verdict

toolset = Toolset(name='더미-세계', instructions='안내문')


@toolset.tool
def propose_thing(actor, amount: int = 1):
    """무언가를 제안한다."""
    return Verdict(kind=Verdict.ALLOW), Outcome.COMMITTED, {}


class 열쇠고리:
    """인증 진입점이 클래스 속성인 흔한 모양(`APIToken.authenticate`)."""

    열린_열쇠 = '맞는-열쇠'

    @classmethod
    def authenticate(cls, raw):
        from django.contrib.auth.models import User

        if raw != cls.열린_열쇠:
            return None
        return User.objects.get_or_create(username='직원')[0]
