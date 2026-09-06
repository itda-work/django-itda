"""`manage.py mcp_stdio` — 붙는 방식이 이 명령의 전부다.

실제로 stdio 를 돌리지는 않는다(그건 클라이언트가 있어야 한다). 여기서 재는 것은
셋이다 — 자리를 열쇠로 얻는가, 열쇠가 없으면 멈추는가, **stdout 을 더럽히지 않는가**.
"""

import io
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

ITDA = {'TOOLSET': 'tests.world.toolset', 'AUTHENTICATE': 'tests.world.열쇠고리.authenticate'}


def run(monkeypatch, token='맞는-열쇠', **kwargs):
    """서버를 세우는 데까지만 간다 — `run()` 은 가짜다."""
    monkeypatch.setenv('WORLD_TOKEN', token)
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch('django_itda.adapters.fastmcp.build_server') as build:
        call_command('mcp_stdio', stdout=stdout, stderr=stderr, **kwargs)
    return build, stdout.getvalue()


@pytest.mark.django_db
@override_settings(ITDA=ITDA)
def test_열쇠로_자리를_얻어_서버를_세운다(monkeypatch, capsys):
    build, _ = run(monkeypatch)

    toolset, actor_provider = build.call_args.args
    assert toolset.name == '더미-세계'
    assert actor_provider().username == '직원', '프로세스 하나 = 자리 하나다.'
    build.return_value.run.assert_called_once_with()


@pytest.mark.django_db
@override_settings(ITDA=ITDA)
def test_stdout_에_아무것도_찍지_않는다(monkeypatch, capsys):
    _, stdout = run(monkeypatch)
    찍힌_것 = capsys.readouterr()

    assert stdout == ''
    assert 찍힌_것.out == '', 'stdio 전송이 곧 stdout 이다 — 한 줄만 찍어도 대화가 깨진다.'
    assert 'django-itda mcp_stdio' in 찍힌_것.err


@pytest.mark.django_db
@override_settings(ITDA=ITDA)
def test_열쇠가_없으면_멈춘다(monkeypatch):
    with pytest.raises(CommandError) as raised:
        run(monkeypatch, token='')

    assert '세계에 들어갈 열쇠가 없다' in str(raised.value)


@pytest.mark.django_db
@override_settings(ITDA=ITDA)
def test_틀린_열쇠도_멈춘다(monkeypatch):
    with pytest.raises(CommandError) as raised:
        run(monkeypatch, token='없는-열쇠')

    assert '세계가 그 열쇠를 모른다' in str(raised.value)


@pytest.mark.django_db
@override_settings(ITDA={})
def test_설정이_없으면_무엇을_넣어야_하는지_말해_준다(monkeypatch):
    with pytest.raises(CommandError) as raised:
        run(monkeypatch)

    assert 'TOOLSET' in str(raised.value)


@pytest.mark.django_db
@override_settings(ITDA=ITDA)
def test_열쇠가_담긴_환경변수_이름은_바꿀_수_있다(monkeypatch):
    monkeypatch.setenv('다른_열쇠', '맞는-열쇠')
    monkeypatch.delenv('WORLD_TOKEN', raising=False)
    stdout, stderr = io.StringIO(), io.StringIO()

    with mock.patch('django_itda.adapters.fastmcp.build_server'):
        call_command('mcp_stdio', '--token-env', '다른_열쇠', stdout=stdout, stderr=stderr)

    assert stdout.getvalue() == ''
