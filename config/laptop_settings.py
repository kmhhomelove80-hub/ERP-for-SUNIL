"""Isolated, loopback-only laptop trial settings; never use for deployment."""
from .settings import *  # noqa: F403

DEBUG = True
SECRET_KEY = 'isolated-laptop-trial-only'
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'laptop-data' / 'trial.sqlite3'}}
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_PROXY_SSL_HEADER = None
CSRF_TRUSTED_ORIGINS = []
