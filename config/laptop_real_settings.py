"""Separate local database for trials using the user's original Excel files."""
from .laptop_settings import *  # noqa: F403
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'laptop-real-data' / 'trial.sqlite3'}}
