"""accounts 관리자 설정."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Django 기본 UserAdmin을 그대로 쓴다 — 커스텀 필드가 없다."""
