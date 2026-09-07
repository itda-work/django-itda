"""장부를 눈으로 본다 — `just ledger`.

    just ledger              # 최근 20행
    just ledger --call <id>  # 한 호출의 사슬만
    just ledger --limit 50

**판정하지 않는다.** 장부를 표로 찍을 뿐이다. `just race pay SEED-0004` 나
admin 승인 바로 뒤에 보라고 있는 도구다 — 세계가 한 번 움직였는데 장부에
두 줄이 있으면, 그 차이가 이 단계의 소재다.
"""

import unicodedata

from django.core.management.base import BaseCommand

from ledger.models import Event

HEADER = ('시각', '문', '자리', '전이', '대상', '판정', '규칙', '호출')


class Command(BaseCommand):
    help = '장부(ledger.Event)를 표로 찍는다. 판정하지 않는다.'

    def add_arguments(self, parser):
        parser.add_argument('--call', default='', help='이 호출 ID 의 사슬만 본다')
        parser.add_argument('--limit', type=int, default=20, help='몇 행까지 (기본 20)')

    def handle(self, *args, **options):
        events = Event.objects.select_related('actor')
        if options['call']:
            events = events.filter(call_id__startswith=options['call'])
            rows = list(events)
        else:
            # 최근 N 행을 고른 뒤 **시간 순으로 되돌린다** — 장부는 위에서
            # 아래로 읽는 것이고, 사건의 순서가 곧 뜻이기 때문이다.
            rows = list(reversed(list(events.order_by('-at', '-id')[: options['limit']])))

        if not rows:
            self.stdout.write('장부가 비어 있다.')
            return

        table = [HEADER] + [self._row(event) for event in rows]
        widths = [max(self._width(row[i]) for row in table) for i in range(len(HEADER))]
        for index, row in enumerate(table):
            cells = zip(row, widths, strict=True)
            self.stdout.write('  '.join(self._pad(cell, width) for cell, width in cells))
            if index == 0:
                self.stdout.write('  '.join('─' * width for width in widths))
        self.stdout.write(f'\n{len(rows)}행.')

    @staticmethod
    def _row(event):
        return (
            event.at.strftime('%m-%d %H:%M:%S'),
            event.door,
            event.actor_label or '—',
            event.transition,
            event.subject_label,
            event.kind,
            ', '.join(event.rule_ids) or '—',
            event.call_id[:8] or '—',
        )

    @staticmethod
    def _width(text):
        """한글·이모지는 두 칸으로 센다 — 안 그러면 표가 어긋난다."""
        return sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in text)

    @classmethod
    def _pad(cls, text, width):
        return text + ' ' * (width - cls._width(text))
