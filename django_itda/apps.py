from django.apps import AppConfig


class DjangoItdaConfig(AppConfig):
    """도구면 앱 — 이 앱이 설치돼야 궤적(`ToolCall`)이 남는다."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'django_itda'
    verbose_name = '도구면'
