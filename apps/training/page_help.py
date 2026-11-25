"""Which guide explains the page someone is on, for the "?" button in the top bar."""
from django.urls import reverse

# Pages with an id in the address, which guides can't list as a fixed page.
BY_VIEW = {
    "participants:staff-profile": ["read-a-profile"],
    "recruiting:position": ["connect-a-candidate"],
    "recruiting:candidate-detail": ["connect-a-candidate"],
    "recruiting:select-candidate": ["connect-a-candidate"],
    "recruiting:disclosure": ["connect-a-candidate"],
    "recruiting:candidate": ["connect-a-candidate"],
    "customers:need": ["add-a-position", "approvals"],
    "customers:need-new": ["add-a-position"],
    "customers:need-edit": ["add-a-position"],
    "customers:need-action": ["add-a-position", "approvals"],
    "events:event_detail": ["staff-an-event", "close-out-an-event", "your-schedule"],
    "operations:event": ["staff-an-event", "close-out-an-event", "your-schedule"],
    "customer_portal:recruiting-candidate": ["customer-review-candidates"],
    "customer_portal:need": ["customer-request-position"],
    "customer_portal:need-create": ["customer-request-position"],
    "customer_portal:need-edit": ["customer-request-position"],
    "participants:portal-event": ["participant-sign-up-for-an-event"],
    "participants:portal-profile-edit": ["participant-update-profile"],
}
SKIP = {"security:help", "training:guide", "training:handbook", "training:mine", "participants:portal-help",
        "participants:portal-help-guide", "customer_portal:help-guide", "training:help-requests"}


def _portal(path):
    if path.startswith("/participants/portal/"):
        return "participant"
    if path.startswith("/customer/"):
        return "customer"
    return ""


_INDEX = {"at": 0.0, "pages": {}, "guides": {}}


def _index():
    """Guides and their pages, refreshed every minute so admin edits show up without extra queries per page."""
    import time
    from .models import Guide, GuideStep
    if time.monotonic() - _INDEX["at"] > 60:
        pages = {}
        for page, slug in (GuideStep.objects.filter(guide__published=True).exclude(page="")
                           .order_by("guide__position", "position").values_list("page", "guide__slug")):
            pages.setdefault(page, [])
            if slug not in pages[page]:
                pages[page].append(slug)
        _INDEX.update(at=time.monotonic(), pages=pages,
                      guides={g.slug: (g.title, set(g.audiences or [])) for g in Guide.objects.filter(published=True)})
    return _INDEX


def guide_for(request):
    """(url, title) of the best guide for this page, or None."""
    from .access import audiences_for
    match = getattr(request, "resolver_match", None)
    view = match.view_name if match else ""
    if view in SKIP or (view == "customer_portal:workspace" and (match.kwargs or {}).get("tab") == "help"):
        return None
    portal = _portal(request.path)
    allowed = {portal} if portal else (audiences_for(request.user) & {"admin", "manager", "recruiter", "event_staff"})
    if not allowed:
        return None
    index = _index()
    wanted = list(BY_VIEW.get(view, [])) + index["pages"].get(request.path, [])
    for slug in wanted:
        title, audiences = index["guides"].get(slug, ("", set()))
        if title and allowed & audiences:
            if portal == "participant":
                url = reverse("participants:portal-help-guide", args=[slug])
            elif portal == "customer":
                url = reverse("customer_portal:help-guide", args=[slug])
            else:
                url = reverse("training:guide", args=[slug])
            return url, title
    return None
