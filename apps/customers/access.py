"""Customer authorization is explicit staff authority, never a broad is_staff test."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from apps.accounts.roles import manager


def require_manager(actor, *, lock=False):
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "pk", None):
        raise PermissionDenied("BTW Administrator or Manager authority is required.")
    query = get_user_model().objects
    if lock:
        # Match staff account/role mutation lock order, including cross-company
        # identity preparation. Organization rows still protect domain revisions.
        from apps.accounts.roles import lock_role_groups
        lock_role_groups()
        query = query.select_for_update()
    fresh = query.filter(pk=actor.pk).first()
    if fresh is None or not manager(fresh):
        raise PermissionDenied("BTW Administrator or Manager authority is required.")
    if not fresh.is_superuser and not fresh.has_perm("customers.manage_customers"):
        raise PermissionDenied("The reviewed customer-management role definition must be configured.")
    return fresh


def is_manager(actor):
    try:
        require_manager(actor)
        return True
    except PermissionDenied:
        return False


def organizations(actor):
    require_manager(actor)
    from .models import CustomerOrganization
    return CustomerOrganization.objects.all()


def customer_identity(user):
    from .models import CustomerIdentity
    return bool(user and user.pk and CustomerIdentity.objects.filter(user_id=user.pk).exists())
