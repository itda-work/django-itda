"""회원 모델."""

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """사용자. 점주·AI 직원·고객이 모두 같은 모델을 쓴다."""

    class Meta(AbstractUser.Meta):
        verbose_name = '사용자'
        verbose_name_plural = '사용자'

    def __str__(self):
        return self.username
