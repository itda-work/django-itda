"""itda-django 설정.

교육용 단계별 프로젝트 — 외부 서비스 없이 SQLite 하나로 돈다.
설정은 일부러 최소로 둔다. 단계가 올라가며 필요한 법을 하나씩 켠다.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 교육용 로컬 전용 키. 운영에 쓰지 않는다.
SECRET_KEY = 'django-insecure-itda-django-교육용-키-운영-사용-금지'

DEBUG = True

ALLOWED_HOSTS = []

# 공유 세계 모드 — 옆자리 학생이 내 세계에 요청을 보낼 수 있게 문을 연다(5단계).
# 여는 것은 **호스트 검사까지**다. 누가 들어와서 누구의 주문을 볼 수 있는지는
# 여전히 토큰과 권한이 답하고, 소유권 스코핑은 6단계의 몫이다.
if os.environ.get('WORLD_SHARED') == '1':
    ALLOWED_HOSTS = ['*']


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # local apps
    'accounts',
    'shop',
    'orders',
    'agent',
]

MIDDLEWARE = [
    # 뷰에서 올라온 잠금 실패를 503 으로 옮긴다(5단계).
    'config.middleware.WorldBusyMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # 기본은 저장소의 dev DB 하나다. `ITDA_DB` 를 주면 그 파일을 쓴다 —
        # 공유 세계·검증용 별도 파일을 관찰 중인 dev DB 와 섞지 않기 위해서다.
        'NAME': Path(os.environ.get('ITDA_DB') or BASE_DIR / 'db.sqlite3'),
        # 테스트 DB 를 **파일**로 둔다. 기본값(인메모리 shared-cache)에서는 두
        # 스레드가 같은 테이블을 만지는 순간 `database table is locked` 가
        # 도메인 판정보다 **먼저** 난다. 그러면 5단계 경합 테스트의 실패가
        # "세계가 못 막았다"인지 "잠금에 걸렸다"인지 구분되지 않는다.
        'TEST': {'NAME': BASE_DIR / '.test_db.sqlite3'},
        'OPTIONS': {
            # 잠금 실패의 **빈도를 줄이는** 설정이다. 없애는 것이 아니다.
            #
            # - `IMMEDIATE`: 트랜잭션을 열 때부터 쓰기 잠금을 잡는다. DEFERRED 는
            #   읽고 나서 쓰려 할 때 잠금 승격에 실패하면 기다려 주지 않고
            #   `database is locked` 를 즉시 돌려준다.
            # - `timeout`: 남이 쥔 잠금을 5초까지 기다린다.
            # - WAL: 읽는 쪽이 쓰는 쪽을 막지 않는다.
            #
            # 그래도 잠금 실패는 남는다. 그래서 `config/middleware.py` 가 그 실패를
            # 503 으로 번역한다 — 설정은 완화이고, 정직한 응답이 보장이다.
            'transaction_mode': 'IMMEDIATE',
            'timeout': 5,
            'init_command': 'PRAGMA journal_mode=WAL;',
        },
    }
}


AUTH_USER_MODEL = 'accounts.User'

# AI 직원은 is_staff 가 아니라 admin 로그인 화면을 쓸 수 없다 — 콘솔 로그인으로 보낸다.
LOGIN_URL = '/agent/login/'
LOGIN_REDIRECT_URL = '/agent/'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
