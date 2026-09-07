"""같은 요청 두 개를 **동시에** 쏜다 — 경합을 눈으로 보는 도구(5단계).

채점표(`tests/stage_05_contention.py`)와 같은 것을 보지만, 여기는 테스트가 아니라
관찰 도구다. 판정하지 않는다 — 세계의 대답 두 개를 나란히 놓을 뿐이다.

    just race refund SEED-0003 --amount 30000   # 환불 제안 2개 동시
    just race pay SEED-0004                     # 결제 2개 동시
    just race approve 3                         # 점주 확정 2개 동시

모드가 둘이다.

- **in-process(기본)** — 결정적이다. 채점 테스트와 같은 `threading.Barrier` 훅으로
  스레드 둘을 서비스 함수 직전에 만나게 했다가 동시에 풀어 준다. 매번 겹친다.
  **이 모드는 `db.sqlite3` 를 직접 만진다.** 세계가 더러워지면 `just reset-db` 로
  되돌린다(다른 파일에서 보고 싶으면 `ITDA_DB=/tmp/race.sqlite3` 를 준다).
- **`--http`** — 확률적이다. 진짜 HTTP 요청 두 개를 Barrier 로 맞춰 쏜다. **`refund`·`approve`
  만 받는다** — 6단계부터 결제의 확정자는 고객이고, 고객의 결제 페이지는 세션으로 여는
  사람의 자리라 `ai-staff` 토큰으로 쏠 수 없다(토큰 API 는 이제 링크만 준다). 결제 경합은
  in-process 모드로 본다. 창이
  좁아 매번 겹치지는 않으므로 `--rounds` 로 반복하고, **회차마다 대상을 새로
  만든다**(`target` 은 장바구니 원본으로 쓴다). 같은 주문에 계속 쏘면 2회차부터는
  경합과 무관하게 `ALREADY` 가 나와 회차를 세는 의미가 없다. 옆자리 학생의
  세계로 쏘려면 `--url http://<상대 IP>:8000` (상대는 `just run-shared` 로 띄운다).
"""

import json
import os
import threading
import urllib.error
import urllib.request
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from accounts.models import APIToken
from orders import services
from orders.models import InvalidTransition, Order, Refund
from orders.verdict import Verdict

User = get_user_model()

LABELS = ('A', 'B')


class Command(BaseCommand):
    help = '같은 요청 두 개를 동시에 보내 경합을 관찰한다 (refund / pay / approve).'

    def add_arguments(self, parser):
        parser.add_argument('mode', choices=('refund', 'pay', 'approve'))
        parser.add_argument('target', help='주문번호(refund·pay) 또는 환불 ID(approve)')
        parser.add_argument('--amount', type=int, default=None, help='환불 금액 (기본: 전액)')
        parser.add_argument('--http', action='store_true', help='진짜 HTTP 요청으로 쏜다')
        parser.add_argument('--rounds', type=int, default=5, help='--http 모드의 반복 횟수')
        parser.add_argument(
            '--url', default='http://127.0.0.1:8000', help='--http 모드가 두드릴 세계의 주소'
        )

    def handle(self, *args, **options):
        if options['http']:
            if options['mode'] == 'pay':
                raise CommandError(
                    '6단계부터 결제는 고객 세션 — refund/approve 만 --http. '
                    '결제 경합은 --http 없이 in-process 로 본다(just race pay SEED-0004).'
                )
            self._race_http(options)
        else:
            self._race_in_process(options)

    # --- in-process — 결정적 -------------------------------------------------

    def _race_in_process(self, options):
        """서비스 함수를 스레드 둘이 직접 부른다. 만나는 지점은 트랜잭션 **밖**이다.

        훅을 트랜잭션 안에 두면 두 스레드가 서로의 잠금을 기다리다 timeout 까지
        멈춘다. 그래서 판정 직전·전이 직전처럼 아직 아무것도 열리지 않은 자리에
        Barrier 를 끼운다.
        """
        mode, target = options['mode'], options['target']
        barrier = threading.Barrier(2, timeout=10)
        lock = threading.Lock()
        lines = []

        if mode == 'refund':
            order = self._order(target)
            amount = options['amount'] or order.total_amount
            owner_of_hook, hook = Refund, 'decide'

            def work():
                actor = User.objects.get(username='ai-staff')
                verdict, outcome = services.propose_refund(
                    actor, Order.objects.get(pk=order.pk), amount, '경합 관찰'
                )
                return verdict, f'{outcome.state} · {verdict.reason}'
        elif mode == 'pay':
            order = self._order(target)
            owner_of_hook, hook = Order, 'mark_paid'

            def work():
                verdict = services.pay_order(Order.objects.get(pk=order.pk))
                return verdict, verdict.reason
        else:
            refund_pk = self._refund_pk(target)
            owner_of_hook, hook = Refund, 'approve'

            def work():
                boss = User.objects.get(username='owner')
                refund = Refund.objects.select_related('order').get(pk=refund_pk)
                try:
                    refund.approve(boss)
                except InvalidTransition as denied:
                    return denied.verdict, denied.verdict.reason
                verdict = Verdict(
                    kind=Verdict.ALLOW,
                    reason=f'{refund.order.order_number} 환불 {refund.amount:,}원을 승인했습니다.',
                )
                return verdict, verdict.reason

        def run(index):
            try:
                verdict, note = work()
                line = self._line(index, verdict.status_code, verdict.kind, note, verdict.rule_ids)
            except Exception as exc:  # noqa: BLE001 — 판정이 아닌 실패도 그대로 보여 준다
                line = f'[{LABELS[index]}] --- {type(exc).__name__}  {exc}'
            finally:
                connection.close()
            with lock:
                lines.append((index, line))

        with self._gate(barrier, owner_of_hook, hook):
            threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        for _, line in sorted(lines):
            self.stdout.write(line)

    @staticmethod
    def _gate(barrier, owner, name):
        """`owner.name` 호출 직전에 Barrier 를 끼운다 — 앱 코드는 건드리지 않는다."""
        attribute = owner.__dict__.get(name)
        if isinstance(attribute, classmethod):
            function = attribute.__func__

            def gated_classmethod(cls, *args, **kwargs):
                barrier.wait()
                return function(cls, *args, **kwargs)

            return mock.patch.object(owner, name, classmethod(gated_classmethod))

        original = getattr(owner, name)

        def gated(self, *args, **kwargs):
            barrier.wait()
            return original(self, *args, **kwargs)

        return mock.patch.object(owner, name, gated)

    # --- HTTP — 확률적 -------------------------------------------------------

    def _race_http(self, options):
        """진짜 요청 두 개를 맞춰 쏜다. 겹치는 창이 좁아 회차마다 결과가 다르다.

        **회차마다 대상을 새로 만든다.** 같은 주문에 계속 쏘면 2회차부터는
        이미 처리된 건이 있어 경합과 무관하게 `ALREADY`·409 가 나온다 — 그러면
        회차를 반복하는 의미가 없다. `target` 은 **장바구니 원본**으로 쓴다.
        그 주문의 품목을 그대로 새 주문으로 접수하고, 모드에 맞게 결제·제안까지
        준비한 다음 그 새 대상에 두 요청을 쏜다.

        준비 요청은 AI 직원 자리에서 한다(주문 접수·환불 제안은 그쪽 업무다).
        경합 요청만 모드의 자리에서 쏜다 — `approve` 는 점주다.

        임시 토큰을 발급해 쓰고 끝나면 지운다. 다른 사람의 세계(`--url`)로 쏠 때는
        그쪽에서 받은 열쇠를 `WORLD_TOKEN` 환경변수로 준다 — 내 DB 의 토큰은
        남의 세계에서 통하지 않는다(그때는 준비도 그 열쇠로 하므로, 자리가
        모자라면 준비 단계에서 403 이 그대로 보인다).
        """
        base = options['url'].rstrip('/')
        borrowed = os.environ.get('WORLD_TOKEN', '')
        account = 'owner' if options['mode'] == 'approve' else 'ai-staff'
        issued = []
        if borrowed:
            setup_token = race_token = borrowed
        else:
            setup_token = self._issue('ai-staff', issued)
            race_token = setup_token if account == 'ai-staff' else self._issue(account, issued)

        try:
            items = self._remote_items(base, setup_token, options['target'])
            for round_number in range(1, options['rounds'] + 1):
                method, path, payload, subject = self._prepare(base, setup_token, items, options)
                self.stdout.write(f'— {round_number}회차 ({subject})')
                lines = self._one_http_round(base, race_token, method, path, payload)
                for _, line in sorted(lines):
                    self.stdout.write(line)
        finally:
            for row in issued:
                row.delete()

    @staticmethod
    def _issue(username, issued):
        row, raw = APIToken.issue(User.objects.get(username=username), 'race(임시)')
        issued.append(row)
        return raw

    def _one_http_round(self, base, token, method, path, payload):
        barrier = threading.Barrier(2, timeout=10)
        lock = threading.Lock()
        lines = []

        def run(index):
            barrier.wait()
            try:
                status, body = self._request(base, token, method, path, payload)
            except Exception as exc:  # noqa: BLE001 — 닿지 못한 것도 관찰 대상이다
                with lock:
                    lines.append((index, f'[{LABELS[index]}] --- {type(exc).__name__}  {exc}'))
                return
            note = body.get('reason') or body.get('error', '')
            if body.get('outcome'):
                note = f'{body["outcome"]} · {note}'
            line = self._line(index, status, body.get('kind', ''), note, body.get('rule_ids'))
            with lock:
                lines.append((index, line))

        threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        return lines

    # --- 회차 준비 -----------------------------------------------------------

    def _prepare(self, base, token, items, options):
        """이번 회차의 대상을 새로 만들고 `(method, path, payload, 설명)` 을 돌려준다.

        주문을 **결제까지 하지는 않는다.** 6단계부터 토큰 API 는 결제 링크만 주고
        결제는 고객 세션의 몫이다. 새 주문은 `pending` 인 채로 남는데, 환불 판정은
        주문 상태를 묻지 않으므로(`Refund.decide` — 기한과 금액만 본다) 이 트랙이
        보려는 INSERT·UPDATE 경합은 그대로 재현된다.
        """
        order = self._post(base, token, '/api/orders/', {'items': items})['order']
        subject = f'새 주문 {order["order_number"]} (결제 대기)'
        payload = {'order_id': order['id'], 'reason': '경합 관찰'}
        if options['amount']:
            payload['amount'] = options['amount']
        if options['mode'] == 'refund':
            return 'POST', '/api/refunds/', payload, subject

        refund = self._post(base, token, '/api/refunds/', payload)['refund']
        return 'POST', f'/api/refunds/{refund["id"]}/approve/', None, f'{subject} · 환불 제안'

    def _remote_items(self, base, token, order_number):
        """`target` 주문의 품목을 읽어 온다 — 회차마다 같은 장바구니를 다시 쓴다."""
        pk = self._remote_order_pk(base, token, order_number)
        _, detail = self._request(base, token, 'GET', f'/api/orders/{pk}/', None)
        return [
            {'product': item['product_name'], 'quantity': item['quantity']}
            for item in detail['items']
        ]

    def _post(self, base, token, path, payload):
        status, body = self._request(base, token, 'POST', path, payload)
        if status not in (200, 201, 202):
            raise CommandError(f'회차 준비 실패 — {path} 가 HTTP {status} 로 답했다: {body}')
        return body

    @staticmethod
    def _request(base, token, method, path, payload):
        request = urllib.request.Request(
            f'{base}{path}',
            data=json.dumps(payload).encode() if payload is not None else b'',
            method=method,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def _remote_order_pk(self, base, token, order_number):
        """주문은 **원격 세계의 목록에서** 찾는다.

        내 DB 의 pk 를 남의 세계에 그대로 쓰면 다른 주문을 건드린다.
        """
        _, body = self._request(base, token, 'GET', '/api/orders/', None)
        for order in body.get('orders', []):
            if order['order_number'] == order_number:
                return order['id']
        raise CommandError(f'{base} 의 세계에 주문 {order_number} 이(가) 없다.')

    # --- 표현 ----------------------------------------------------------------

    @staticmethod
    def _line(index, status, kind, note, rule_ids=None):
        rules = f' [{", ".join(rule_ids)}]' if rule_ids else ''
        return f'[{LABELS[index]}] {status} {kind or "—"}{rules}  {note}'

    @staticmethod
    def _order(order_number):
        order = Order.objects.filter(order_number=order_number).first()
        if order is None:
            raise CommandError(f'주문 {order_number} 이(가) 없다. just reset-db 로 세계를 심어라.')
        return order

    @staticmethod
    def _refund_pk(target):
        try:
            pk = int(target)
        except ValueError:
            raise CommandError('approve 모드의 target 은 환불 ID(정수)다.') from None
        if not Refund.objects.filter(pk=pk).exists():
            raise CommandError(f'환불 {pk} 번이 없다.')
        return pk
