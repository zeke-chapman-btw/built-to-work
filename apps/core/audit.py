from django.contrib.contenttypes.models import ContentType

from .models import AuditLog


def record_audit(
    *,
    actor,
    action,
    instance,
    source="",
    old_data=None,
    new_data=None,
    reason="",
):
    """Explicitly record an audit event for a persisted model instance."""
    if instance.pk is None:
        raise ValueError("Audit events require a persisted instance.")

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        content_type=ContentType.objects.get_for_model(
            instance, for_concrete_model=False
        ),
        object_id=str(instance.pk),
        source=source,
        old_data=old_data,
        new_data=new_data,
        reason=reason,
    )