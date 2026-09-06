"""API 토큰 발급 — 실접속(live) 트랙의 열쇠를 만든다.

원문 키는 **이 명령의 출력에서 딱 한 번** 나타난다. DB 에는 해시만 남으므로
잃어버리면 다시 발급해야 한다. 콘솔 화면에도, seed_world 에도 심지 않는다 —
세계를 다시 심을 때마다 같은 열쇠가 되살아나면 그건 열쇠가 아니라 장식이다.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.models import APIToken

User = get_user_model()


class Command(BaseCommand):
    help = 'API 토큰을 발급한다. 원문 키는 발급 시 한 번만 출력된다.'

    def add_arguments(self, parser):
        parser.add_argument('username', help='토큰을 받을 계정 (예: ai-staff)')
        parser.add_argument('--name', default='live', help='용도 메모 (예: claude-code)')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError(
                f'{options["username"]} 계정이 없습니다. just setup 으로 세계를 먼저 심으세요.'
            ) from None

        token, raw = APIToken.issue(user, options['name'])
        self.stdout.write(f'  계정 {user.username} · 용도 {token.name}')
        self.stdout.write(self.style.SUCCESS(f'  WORLD_TOKEN={raw}'))
        self.stdout.write('  이 키는 다시 볼 수 없습니다. 지금 복사해 두세요.')
