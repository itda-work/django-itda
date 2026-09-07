"""결제 링크의 만료 — 법을 새로 쓰지 않는다. Django 가 이미 가진 것을 빌린다.

비밀번호 재설정 링크가 3일 뒤 죽는 그 코드가 결제 링크를 1시간 뒤 죽인다.
`django.contrib.auth.tokens.PasswordResetTokenGenerator` 를 그대로 상속하고,
바꾸는 것은 셋뿐이다.

- `key_salt` — **반드시 따로 준다.** 부모의 salt 를 쓰면 비밀번호 토큰과 결제
  토큰이 같은 키 공간에 섞인다. 한쪽에서 만든 토큰이 다른 쪽에서 통할 이유가 없다.
- `_make_hash_value` — 해시에 무엇을 섞는가. 여기가 이 파일의 전부다(아래).
- `check_token` — 부모가 `settings.PASSWORD_RESET_TIMEOUT` 을 **박아 두어**
  이 메서드만 다시 쓴다. 원문을 그대로 옮기고, 마지막 비교만 원문의 동치 변형이다
  (원문 `> TIMEOUT → False`, 여기 `<= self.timeout`).

`make_token`·`_make_token_with_timestamp`·`_num_seconds`·`_now` 는 상속한다.
`_now` 는 원문 주석 그대로 "Used for mocking in tests" — 시험에서 시계를 돌릴 때
`timezone.now` 가 아니라 **이것**만 돌린다.

**무상태다.** 발급한 링크를 어디에도 저장하지 않는다. 그래서 취소할 수도 없고,
누가 언제 발급했는지도 안 남는다(7단계 장부의 소재). 대신 서명 하나로 만료와
상태 결합이 동시에 온다.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int

from .rules import PAY_LINK_TIMEOUT


class PaymentLinkTokenGenerator(PasswordResetTokenGenerator):
    """주문 하나에 대한 결제 링크 토큰."""

    key_salt = 'orders.tokens.PaymentLinkTokenGenerator'

    @property
    def timeout(self):
        """법의 숫자가 기본이고, settings 는 관찰용 노브다.

        `PAYMENT_LINK_TIMEOUT=5 just run` 으로 5초짜리 링크를 만들어 본다 —
        1시간을 기다리지 않기 위해서다. 노브가 없으면 법의 숫자가 산다.
        """
        return getattr(settings, 'PAYMENT_LINK_TIMEOUT', None) or PAY_LINK_TIMEOUT

    def _make_hash_value(self, order, timestamp):
        """토큰에 **무엇을 섞는가** — 바뀌면 결제 조건이 달라지는 것만 섞는다.

        `order.status` 가 핵심이다. 비밀번호 재설정에서 `user.password` 가 하던
        역할을 여기서는 상태가 한다 — 결제가 끝나 `paid` 가 되는 순간 같은 토큰의
        해시가 달라지고, **링크가 스스로 죽는다.** 만료를 기다릴 필요가 없다.

        `updated_at` 은 **넣지 않는다.** 넣으면 어떤 `save()` 도(재고 정정이든
        주소 수정이든) 고객이 들고 있는 링크를 죽인다. 결제 조건과 무관한 변경이
        링크를 끊으면, 그건 보안이 아니라 고장이다.
        """
        return f'{order.pk}{order.status}{order.total_amount}{order.user_id}{timestamp}'

    def check_token(self, order, token):
        """부모 원문을 그대로 옮기고, 마지막 비교만 이 클래스의 `timeout` 을 본다.

        부모는 `settings.PASSWORD_RESET_TIMEOUT` 을 직접 읽는다 — 훅이 없어서
        메서드를 통째로 다시 쓴다. 상속이 늘 한 줄로 끝나지는 않는다는 것도
        오늘 보는 것 하나다.

        마지막 줄은 원문의 동치 변형이다 — Django 원문은
        `(now - ts) > TIMEOUT` 이면 `False` 를 돌려주고, 여기서는 같은 조건을
        뒤집은 `<= self.timeout` 을 그대로 돌려준다. 경계는 같다
        (정확히 3600초는 **유효**하다).
        """
        if not (order and token):
            return False
        try:
            ts_b36, _ = token.split('-')
        except ValueError:
            return False
        try:
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False

        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(
                self._make_token_with_timestamp(order, ts, secret),
                token,
            ):
                break
        else:
            return False

        return (self._num_seconds(self._now()) - ts) <= self.timeout

    def expires_at(self, token):
        """이 토큰이 언제 닫히는가 — **화면에 보여 줄 정보용**이다.

        판정은 `check_token` 이 한다. 여기서 서명을 확인하지 않는 이유가 그것이다 —
        위조된 토큰이면 어차피 `check_token` 이 먼저 끊고, 이 값은 화면에 뜨지 않는다.
        """
        try:
            ts = base36_to_int(token.split('-')[0])
        except (ValueError, IndexError):
            return None
        naive = datetime(2001, 1, 1) + timedelta(seconds=ts + self.timeout)
        return timezone.make_aware(naive)


# 모듈 인스턴스 하나. `default_token_generator` 와 공유하지 않는다 — salt 가 다르고,
# 다르다는 것이 요점이다.
payment_token = PaymentLinkTokenGenerator()
