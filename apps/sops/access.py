"""Who can read and who can manage SOPs."""
from apps.accounts.roles import active_staff, has_role, manager


def can_manage(user):
    return active_staff(user) and (user.is_superuser or user.has_perm("sops.manage_sops"))


def visible(user, queryset=None):
    from .models import SOP
    qs = (queryset if queryset is not None else SOP.objects.all()).filter(is_active=True)
    if not active_staff(user):
        return qs.none()
    if user.is_superuser or manager(user) or can_manage(user):
        return qs
    allowed = ["all"]
    if has_role(user, "event_staff"):
        allowed.append("event_staff")
    if has_role(user, "recruiter"):
        allowed.append("recruiter")
    return qs.filter(audience__in=allowed)


def can_read(user):
    return active_staff(user)
