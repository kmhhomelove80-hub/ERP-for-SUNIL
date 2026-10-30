"""Serialize mutations for the initial single-warehouse deployment.

PostgreSQL transaction-scoped lock avoids opposite-direction movement deadlocks
and races between initial import, arrival and allocation changes. SQLite is only
for development; it does not validate production concurrency.
"""
from functools import wraps
from contextlib import contextmanager
from django.db import connection, transaction

@contextmanager
def warehouse_lock():
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(71009301)')
        yield


def warehouse_transaction(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with warehouse_lock():
            return function(*args, **kwargs)
    return wrapped
