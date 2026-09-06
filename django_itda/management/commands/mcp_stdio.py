"""`manage.py mcp_stdio` — Django 프로젝트를 MCP 서버로 세운다.

붙는 방식이 이 명령의 전부다.

- **자리는 프로세스가 정한다.** 환경변수의 열쇠로 한 번 인증하고, 그 자리를
  고정한다. 프로세스 하나 = 자리 하나다. 모델은 도구 인자로 자기 자리를 바꿀
  수 없다 — 바꿀 수 있으면 권한 검사가 장식이 된다.
- **stdout 에 아무것도 찍지 않는다.** stdio 전송이 곧 stdout 이다. 여기에 한 줄만
  찍어도 클라이언트는 JSON-RPC 를 못 읽는다. 사람에게 할 말은 전부 stderr 로.

설정은 `settings.ITDA` 두 열쇠다.

    ITDA = {
        'TOOLSET': 'agent.live.tools.toolset',
        'AUTHENTICATE': 'accounts.models.APIToken.authenticate',
    }

`AUTHENTICATE` 는 원문 열쇠를 받아 사용자 또는 `None` 을 돌려주는 무엇이면 된다.
패키지는 인증 방식을 정하지 않는다 — 토큰을 어떻게 세는지는 프로젝트의 법이다.
"""

import os
import sys
from importlib import import_module

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_TOKEN_ENV = 'WORLD_TOKEN'


def import_dotted(path):
    """`'a.b.C.d'` 를 푼다 — `import_string` 과 달리 **클래스 속성까지** 내려간다.

    설정에 적히는 것이 `accounts.models.APIToken.authenticate` 같은 분류 메서드다.
    `import_string` 은 `모듈.이름` 한 겹만 풀어서 여기서 멈춘다. 프로젝트가
    인증 진입점을 클래스에 두는 것은 흔한 일이라 그 한 겹을 더 내려간다.
    """
    parts = path.split('.')
    for split in range(len(parts) - 1, 0, -1):
        try:
            found = import_module('.'.join(parts[:split]))
        except ImportError:
            continue
        for attribute in parts[split:]:
            found = getattr(found, attribute)
        return found
    raise ImportError(f'{path} 를 임포트할 수 없다.')


class Command(BaseCommand):
    help = '선언된 도구면을 MCP(stdio) 서버로 띄운다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--token-env',
            default=DEFAULT_TOKEN_ENV,
            help=f'열쇠가 담긴 환경변수 이름 (기본 {DEFAULT_TOKEN_ENV})',
        )

    def handle(self, *args, **options):
        config = getattr(settings, 'ITDA', None) or {}
        toolset = self._load(config, 'TOOLSET')
        authenticate = self._load(config, 'AUTHENTICATE')

        variable = options['token_env']
        raw = (os.environ.get(variable) or '').strip()
        if not raw:
            raise CommandError(
                f'세계에 들어갈 열쇠가 없다 — 환경변수 {variable} 이(가) 비어 있다. '
                'manage.py issue_token 으로 발급해 등록 설정에 넣어라.'
            )
        actor = authenticate(raw)
        if actor is None:
            raise CommandError(f'세계가 그 열쇠를 모른다 — {variable} 의 값을 확인하라.')

        # 임포트를 여기서 한다. fastmcp 가 없어도 이 명령을 부르기 전까지는
        # Django 가 뜨는 데 지장이 없어야 한다(선택 의존).
        from django_itda.adapters.fastmcp import build_server

        server = build_server(toolset, lambda: actor)
        print(
            f'django-itda mcp_stdio — toolset={toolset.name} actor={actor}',
            file=sys.stderr,
        )
        server.run()

    def _load(self, config, key):
        path = config.get(key)
        if not path:
            raise CommandError(
                f'settings.ITDA 에 {key} 가 없다 — '
                "예: ITDA = {'TOOLSET': 'agent.live.tools.toolset', "
                "'AUTHENTICATE': 'accounts.models.APIToken.authenticate'}"
            )
        return import_dotted(path)
