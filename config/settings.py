import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('ERP_DEBUG', '1') == '1'
SECRET_KEY = os.getenv('ERP_SECRET_KEY', 'local-development-only-do-not-use-in-production')
if not DEBUG and SECRET_KEY.startswith('local-development'):
    raise RuntimeError('ERP_SECRET_KEY is required outside development')
ALLOWED_HOSTS = os.getenv('ERP_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'inventory']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.getenv('ERP_SQLITE_PATH', str(BASE_DIR / 'dev.sqlite3'))}}
if os.getenv('ERP_DB_NAME'):
    DATABASES['default'] = {'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ['ERP_DB_NAME'], 'USER': os.environ['ERP_DB_USER'], 'PASSWORD': os.environ['ERP_DB_PASSWORD'], 'HOST': os.getenv('ERP_DB_HOST', 'localhost'), 'PORT': os.getenv('ERP_DB_PORT', '5432')}
LANGUAGE_CODE = 'ko-kr'
TIME_ZONE = 'Asia/Seoul'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

STATIC_ROOT = BASE_DIR / 'staticfiles'
CSRF_TRUSTED_ORIGINS = [value for value in os.getenv('ERP_CSRF_TRUSTED_ORIGINS','').split(',') if value]
AUTH_PASSWORD_VALIDATORS = [
    {'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':10}},
    {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
if os.getenv('ERP_DB_SSLMODE') and DATABASES['default']['ENGINE'].endswith('postgresql'):
    DATABASES['default']['OPTIONS'] = {'sslmode':os.environ['ERP_DB_SSLMODE']}
if os.getenv('ERP_TRUST_PROXY','0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv('ERP_ENFORCE_HTTPS','1') == '1'
    SECURE_HSTS_SECONDS = 3600
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
