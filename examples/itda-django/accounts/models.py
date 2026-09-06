"""회원 모델."""

import hashlib
import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """사용자. 점주·AI 직원·고객이 모두 같은 모델을 쓴다."""

    class Meta(AbstractUser.Meta):
        verbose_name = '사용자'
        verbose_name_plural = '사용자'

    def __str__(self):
        return self.username


class APIToken(models.Model):
    """브라우저 밖에서 세계에 들어오는 열쇠 — 실접속(live) 트랙용.

    세션 쿠키는 브라우저의 물건이다. 별도 프로세스로 도는 MCP 서버에는 쿠키가
    없으므로 `Authorization: Bearer <키>` 헤더로 같은 자리를 얻는다.
    **얻는 것은 자리(User)까지다.** 그 자리에서 무엇을 할 수 있는지는 여전히
    권한이 답한다 — 토큰은 인증이지 인가가 아니다.

    원문 키는 저장하지 않는다. sha256 해시만 남기고, 원문은 발급 순간
    (`manage.py issue_token`) 한 번만 보여 준다. DB 가 새어도 열쇠는 안 샌다.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='소유자',
        on_delete=models.CASCADE,
        related_name='api_tokens',
    )
    key = models.CharField('키 해시', max_length=64, unique=True)
    name = models.CharField('용도', max_length=50)
    created_at = models.DateTimeField('발급일', auto_now_add=True)
    last_used_at = models.DateTimeField('마지막 사용', null=True, blank=True)

    class Meta:
        verbose_name = 'API 토큰'
        verbose_name_plural = 'API 토큰'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} / {self.name}'

    @staticmethod
    def hash_key(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, user, name):
        """토큰을 발급하고 `(토큰, 원문 키)` 를 돌려준다. 원문은 여기서만 존재한다."""
        raw = secrets.token_urlsafe(32)
        token = cls.objects.create(user=user, key=cls.hash_key(raw), name=name)
        return token, raw

    @classmethod
    def authenticate(cls, raw):
        """원문 키로 사용자를 찾는다. 없으면 None — 이유는 말해 주지 않는다."""
        token = cls.objects.select_related('user').filter(key=cls.hash_key(raw)).first()
        if token is None or not token.user.is_active:
            return None
        cls.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return token.user
