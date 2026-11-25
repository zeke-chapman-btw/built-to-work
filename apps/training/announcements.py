"""Sign-in messages: which one, if any, a person should see."""
from django.db.models import Q
from django.utils import timezone

from .access import audiences_for
from .models import Announcement


def _own_audiences(user):
    """The person's own account types (not the extra guide audiences managers and admins get)."""
    found = set()
    if user.is_staff:
        from apps.accounts.roles import roles_for
        roles = roles_for(user)
        if user.is_superuser or "administrator" in roles:
            found.add("admin")
        found |= {r for r in roles if r in ("manager", "recruiter", "event_staff")}
    return found | (audiences_for(user) & {"customer", "participant"})


def due_for(user):
    if not (user and user.is_authenticated and user.is_active):
        return None
    today = timezone.localdate()
    mine = _own_audiences(user)
    if not mine:
        return None
    rows = (Announcement.objects.filter(active=True)
            .filter(Q(starts_on__isnull=True) | Q(starts_on__lte=today))
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
            .exclude(seen__user=user).order_by("created_at"))
    return next((a for a in rows if mine & set(a.audiences or [])), None)
