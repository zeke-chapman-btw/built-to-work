"""Lifecycle safeguards for the existing models; legacy contracts are unaffected."""
from contextlib import contextmanager
from contextvars import ContextVar
from django.core.exceptions import ValidationError

_contract_write = ContextVar("btw_intake_contract_write", default=False)
_profile_write = ContextVar("btw_intake_profile_write", default=False)


@contextmanager
def contract_write():
    token = _contract_write.set(True)
    try:
        yield
    finally:
        _contract_write.reset(token)


@contextmanager
def intake_profile_write():
    token = _profile_write.set(True)
    try:
        yield
    finally:
        _profile_write.reset(token)


def smart(snapshot):
    return isinstance(snapshot, dict) and snapshot.get("schema") == "btw-intake-11b-v1"


def guard_version(instance):
    old = type(instance).objects.filter(pk=instance.pk).first() if instance.pk else None
    managed = smart(instance.snapshot) or bool(old and smart(old.snapshot))
    if not managed:
        return
    if old and smart(old.snapshot) and old.status in {"published", "retired"}:
        fields = ("form_id", "version", "snapshot", "stable_id", "content_hash",
                  "created_by_id", "published_at", "effective_at")
        if any(getattr(old, field) != getattr(instance, field) for field in fields):
            raise ValidationError("Published intake contracts are immutable; create a new draft version.")
    if not _contract_write.get():
        raise ValidationError("Use the permission-checked intake lifecycle service.")


def guard_form(instance):
    old = type(instance).objects.filter(pk=instance.pk).first() if instance.pk else None
    if old and old.is_smart_intake != instance.is_smart_intake:
        raise ValidationError("Create an independent intake form; established forms cannot change contract type.")
    if old and old.is_smart_intake:
        if old.slug != instance.slug or not instance.is_smart_intake:
            raise ValidationError("The identity of an established intake form cannot change.")


def guard_submission(instance):
    if not instance.pk:
        return
    old = type(instance).objects.filter(pk=instance.pk).first()
    if old and old.intake_operation_id:
        fields = ("participant_id", "form_version_id", "event_id", "status", "answers",
                  "answer_snapshot", "age_classification", "submitted_at", "source",
                  "idempotency_key", "intake_operation_id", "intake_metadata")
        if any(getattr(old, key) != getattr(instance, key) for key in fields):
            raise ValidationError("Submitted intake evidence and its identity references are immutable.")
