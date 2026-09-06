"""패키지 테스트용 최소 설정.

여기 있는 것은 `django_itda` 가 돌아가는 데 **정말 필요한 것만**이다.
도메인 앱도, 미들웨어도, 템플릿도 없다 — 패키지가 그것들 없이 서는지를
설정 자체가 시험한다.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-itda-테스트용-키'

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django_itda',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        # 테스트 DB 는 **파일**이다. 어댑터 시험이 도구를 실제로 부르면 그 본문은
        # `sync_to_async` 의 실행 스레드에서 돈다. 기본값(인메모리 shared-cache)
        # 에서는 두 스레드가 같은 표를 만지는 순간 `database table is locked` 가
        # 먼저 터져서, 무엇을 쟀는지 알 수 없게 된다(itda-django 5단계 실측).
        'TEST': {'NAME': BASE_DIR / '.test_db.sqlite3'},
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
