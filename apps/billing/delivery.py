"""What each position paid for, against candidates delivered and hires."""
from django.db.models import Q


def paid_for(need):
    """(candidates, people) paid for across every charge on this position (failed/canceled don't count)."""
    candidates = people = 0
    for c in need.charges.exclude(status__in=["canceled", "failed"]):
        if c.kind == "skilled":
            candidates += c.quantity
        elif c.kind == "general":
            people += c.quantity
    return candidates, people


def counts(need):
    from apps.recruiting.models import CandidateDisclosure, EmployerCandidateFeedback
    shared = CandidateDisclosure.objects.filter(need=need)
    delivered = shared.filter(revoked_at__isnull=True).values("participant_id").distinct().count()
    hired = (EmployerCandidateFeedback.objects.filter(disclosure__need=need, kind="report_hired")
             .values("disclosure__participant_id").distinct().count())
    return delivered, hired


def progress(need):
    candidates, people = paid_for(need)
    delivered, hired = counts(need)
    target = candidates or people
    unit = "candidates" if candidates else "people" if people else ""
    done = hired if (people and not candidates) else delivered
    return {"need": need, "title": (need.effective_revision.title if need.effective_revision_id else "") or "Position",
            "paid_for": target, "unit": unit, "delivered": delivered, "hired": hired,
            "remaining": max(0, target - done) if target else 0, "status": need.get_status_display(),
            "complete": bool(target) and done >= target, "behind": bool(target) and done < target,
            "percent": min(100, round(100 * done / target)) if target else 0}


def position_progress(org):
    from apps.customers.models import WorkforceNeed
    needs = (WorkforceNeed.objects.filter(organization=org).filter(Q(charges__isnull=False) | Q(status="active"))
             .select_related("effective_revision").distinct().order_by("-updated_at")[:40])
    return [progress(n) for n in needs]
