"""테스트 공통 설정."""

import pytest
from django.core.management import call_command


@pytest.fixture
def world(db):
    """seed_world 로 심은 세계."""
    call_command('seed_world', verbosity=0)
