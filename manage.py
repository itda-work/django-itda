#!/usr/bin/env python
"""Django 관리 명령 진입점."""

import os
import sys


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            'Django를 불러오지 못했습니다. 설치 여부와 가상환경 활성화를 확인하세요.'
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
