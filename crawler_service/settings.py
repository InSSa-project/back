import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def load_local_env():
    env_path = BASE_DIR / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(*keys, default=''):
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


def env_bool(key, default=False):
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(key, default=''):
    value = os.environ.get(key, default)
    return [item.strip() for item in value.split(',') if item.strip()]


load_local_env()

DEBUG = env_bool('DEBUG', default=False)
SECRET_KEY = env('SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured('SECRET_KEY environment variable is required.')
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', default='localhost,127.0.0.1,testserver' if DEBUG else '')
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'apps.users',
    'apps.calendar',
    'apps.ai',
    'apps.notices',
    'apps.ocr',
    'apps.notifications',
    'apps.risk',
    'apps.community',
    'sync',
    'schedules',
    'dashboard',
    'common',

]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.users.authentication.JwtAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'crawler_service.middleware.LocalCorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

SESSION_COOKIE_SAMESITE = env('SESSION_COOKIE_SAMESITE', default='Lax')
SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_SECURE = env_bool('CSRF_COOKIE_SECURE', default=not DEBUG)
SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', default=not DEBUG)
SECURE_HSTS_SECONDS = int(env('SECURE_HSTS_SECONDS', default='0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', default=False)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

ROOT_URLCONF = 'crawler_service.urls'

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

WSGI_APPLICATION = 'crawler_service.wsgi.application'

DATABASE_URL = env('DATABASE_URL', default='').strip()
DB_ENGINE = env('DB_ENGINE', default='sqlite').strip().lower()

# Render / Supabase 배포 환경
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=env_bool('DATABASE_SSL_REQUIRE', default=False),
        )
    }

# 로컬 또는 DB_* 개별 환경변수를 사용하는 PostgreSQL 환경
elif DB_ENGINE in {'postgres', 'postgresql'}:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env('DB_NAME'),
            'USER': env('DB_USER'),
            'PASSWORD': env('DB_PASSWORD'),
            'HOST': env('DB_HOST', default='localhost'),
            'PORT': env('DB_PORT', default='5432'),
            'CONN_MAX_AGE': int(env('DB_CONN_MAX_AGE', default='60')),
        }
    }

# 별도 DB 설정이 없는 로컬 개발 환경
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Supabase Storage — used by backfill_notice_images and notice image upload pipeline.
# The service role key is backend-only and must never appear in API responses.
SUPABASE_URL = env('SUPABASE_URL', default='')
SUPABASE_SERVICE_ROLE_KEY = env('SUPABASE_SERVICE_ROLE_KEY', default='')
SUPABASE_STORAGE_BUCKET = env('SUPABASE_STORAGE_BUCKET', default='notices')
SUPABASE_STORAGE_TIMEOUT = int(env('SUPABASE_STORAGE_TIMEOUT', default='30'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = env_list(
    'CORS_ALLOWED_ORIGINS',
    default='http://localhost:5173,http://127.0.0.1:5173' if DEBUG else '',
)
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')
FRONTEND_URL = env('FRONTEND_URL', 'FRONTEND_BASE_URL', default='http://localhost:5173' if DEBUG else '')
FRONTEND_BASE_URL = FRONTEND_URL

CORS_ALLOW_METHODS = [
    'GET',
    'POST',
    'PUT',
    'PATCH',
    'DELETE',
    'OPTIONS',
]
AUTH_USER_MODEL = 'users.User'

JWT_ACCESS_LIFETIME_SECONDS = int(env('JWT_ACCESS_LIFETIME_SECONDS', default=60 * 15))
JWT_REFRESH_LIFETIME_SECONDS = int(env('JWT_REFRESH_LIFETIME_SECONDS', default=60 * 60 * 24 * 14))
JWT_ACCESS_SLIDING_EXPIRATION = env_bool('JWT_ACCESS_SLIDING_EXPIRATION', default=True)
JWT_ACCESS_MAX_LIFETIME_SECONDS = int(env('JWT_ACCESS_MAX_LIFETIME_SECONDS', default=JWT_REFRESH_LIFETIME_SECONDS))

MATTERMOST_BASE_URL = env('MATTERMOST_BASE_URL', default='https://meeting.ssafy.com')
MATTERMOST_TIMEOUT_SECONDS = int(env('MATTERMOST_TIMEOUT_SECONDS', default=5))

GOOGLE_OAUTH_CLIENT_ID = env('GOOGLE_OAUTH_CLIENT_ID', 'GOOGLE_CLIENT_ID')
GOOGLE_OAUTH_CLIENT_SECRET = env('GOOGLE_OAUTH_CLIENT_SECRET', 'GOOGLE_CLIENT_SECRET')
GOOGLE_OAUTH_REDIRECT_URI = env(
    'GOOGLE_OAUTH_REDIRECT_URI',
    'GOOGLE_REDIRECT_URI',
    default='http://localhost:8000/api/v1/users/oauth/google/callback' if DEBUG else '',
)
KAKAO_REST_API_KEY = env('KAKAO_REST_API_KEY', 'KAKAO_CLIENT_ID')
KAKAO_CLIENT_SECRET = env('KAKAO_CLIENT_SECRET')
KAKAO_OAUTH_REDIRECT_URI = env(
    'KAKAO_OAUTH_REDIRECT_URI',
    'KAKAO_REDIRECT_URI',
    default='http://localhost:8000/api/v1/users/oauth/kakao/callback' if DEBUG else '',
)

AI_SERVER_ENABLED = env('AI_SERVER_ENABLED', default='false').lower() == 'true'
AI_SERVER_BASE_URL = env('AI_SERVER_BASE_URL', default='http://localhost:8001')
AI_REQUEST_TIMEOUT = int(env('AI_REQUEST_TIMEOUT', default='20'))

MATTERMOST_BASE_URL = env('MATTERMOST_BASE_URL', default='https://meeting.ssafy.com')
MATTERMOST_TIMEOUT_SECONDS = int(env('MATTERMOST_TIMEOUT_SECONDS', default=5))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'apps.users': {
            'handlers': ['console'],
            'level': env('OAUTH_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
    },
}


