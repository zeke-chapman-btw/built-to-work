"""Which guides a person sees, based on who they are in the app."""
from .models import Guide, GuideProgress


def audiences_for(user):
    if not (user and user.is_authenticated and user.is_active):
        return set()
    found = set()
    if user.is_staff:
        from apps.accounts.roles import roles_for
        roles = roles_for(user)
        if user.is_superuser or "administrator" in roles:
            found.add("admin")
        found |= {r for r in roles if r in ("manager", "recruiter", "event_staff")}
        # Admins and managers oversee everyone, so they get the guides for the people they manage too.
        if "admin" in found:
            found |= {"manager", "recruiter", "event_staff"}
        if "manager" in found:
            found |= {"recruiter", "event_staff"}
    from apps.customers.models import CustomerIdentity
    if CustomerIdentity.objects.filter(user=user).exists():
        found.add("customer")
    try:
        from apps.participants.models import ParticipantAccount
        if ParticipantAccount.objects.filter(user=user).exists():
            found.add("participant")
    except Exception:
        pass
    return found


def guides_for(audiences, *, include_unpublished=False):
    rows = Guide.objects.all() if include_unpublished else Guide.objects.filter(published=True)
    wanted = set(audiences)
    return [g for g in rows.prefetch_related("steps") if wanted & set(g.audiences or [])]


def can_see(user, guide):
    return bool(set(guide.audiences or []) & audiences_for(user)) or bool(user.is_superuser)


def done_ids(user):
    return set(GuideProgress.objects.filter(user=user).values_list("guide_id", flat=True))


def summary(user):
    """Progress on the person's own training list."""
    mine = [g for g in guides_for(audiences_for(user)) if g.required]
    done = done_ids(user)
    finished = sum(1 for g in mine if g.pk in done)
    return {"total": len(mine), "done": finished, "percent": round(100 * finished / len(mine)) if mine else 0}
