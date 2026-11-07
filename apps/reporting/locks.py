"""One short ledger lock serializes source writes with compilation and approval.
Existing source services retain all permission, validation and accounting rules.
Role/bootstrap locks precede this row; event and source rows follow it.
"""
from contextlib import contextmanager
from functools import wraps
from django.db import transaction

@contextmanager
def compensation_lock():
    with transaction.atomic():
        from apps.accounts.roles import lock_role_groups
        from .models import CompensationLedgerLock
        lock_role_groups()
        row, _ = CompensationLedgerLock.objects.get_or_create(pk=1)
        CompensationLedgerLock.objects.select_for_update().get(pk=row.pk)
        yield

def serialized_compensation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with compensation_lock():
            return function(*args, **kwargs)
    return guarded
