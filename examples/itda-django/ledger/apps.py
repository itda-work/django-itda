from django.apps import AppConfig


class LedgerConfig(AppConfig):
    """장부 앱 — 세계가 움직인 사실이 여기 남는다."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ledger'
    verbose_name = '장부'
