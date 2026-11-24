"""Help → a plain-language staff guide, showing the parts for each person's role first."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render


@login_required(login_url="/accounts/login/")
def guide(request):
    user = request.user
    if not user.is_staff:
        raise PermissionDenied
    from apps.accounts.roles import has_role, manager
    mine = {"start"}
    if has_role(user, "event_staff"):
        mine.add("event")
    if has_role(user, "recruiter"):
        mine.add("recruit")
    if manager(user):
        mine.update({"recruit", "event", "manage"})
    show_all = request.GET.get("all") == "1"
    from apps.training.access import audiences_for, done_ids, guides_for
    from apps.training.models import STAFF_AUDIENCES
    guides = guides_for(STAFF_AUDIENCES if show_all else audiences_for(user))
    finished = done_ids(user)
    for g in guides:
        g.is_done = g.pk in finished
    return render(request, "internal/help.html", {"page_title": "Help", "mine": mine, "show_all": show_all,
                                                  "is_manager": manager(user), "guides": guides})
