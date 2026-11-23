"""App usage: sign-ins and which areas people open, split by staff, customers and participants.

Only counts (no clicks, no time-on-page) are kept, and only for 12 months.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.signals import user_logged_in
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Sum
from django.dispatch import receiver
from django.utils import timezone

EASTERN = ZoneInfo("America/New_York")
KEEP_DAYS = 366
SKIP = ("/static/", "/media/", "/health", "/admin/", "/favicon", "/participants/register", "/participants/intake/",
        "/e/", "/api/", "/trailer-control/")
AREAS = (("/participants/portal", "Participant portal"), ("/participants/staff/intake-forms", "Forms"),
         ("/participants/historical", "Participant imports"), ("/participants", "Participants"), ("/crm", "Touchpoints"),
         ("/recruiting", "Recruiting"), ("/customer/", "Customer portal"), ("/customers", "Customers"), ("/events", "Events"),
         ("/staff", "Staffing"), ("/operations/forms", "Forms"), ("/operations", "Operations"), ("/messaging", "Messages"),
         ("/settings", "Settings"), ("/reports", "Reports"), ("/internal/reports", "Reports"), ("/my", "My stuff"),
         ("/tasks", "Tasks"), ("/sops", "SOPs"), ("/approvals", "Approvals"), ("/inbox", "Inbox"), ("/help", "Help"))


def audience(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    if user.is_staff:
        return "staff"
    if hasattr(user, "customer_identity"):
        return "customer"
    if hasattr(user, "participant_account"):
        return "participant"
    return None


def area_for(path):
    if path == "/":
        return "Home"
    for prefix, name in AREAS:
        if path.startswith(prefix):
            return name
    return "Other"


@receiver(user_logged_in)
def record_sign_in(sender, request, user, **kwargs):
    from .models import UsageSignIn
    who = audience(user)
    if who:
        try:
            UsageSignIn.objects.create(user=user, audience=who, at=timezone.now())
        except Exception:  # usage numbers must never stop anyone signing in
            pass


class UsageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if (request.method == "GET" and response.status_code == 200 and not request.path.startswith(SKIP)
                    and "text/html" in response.get("Content-Type", "") and request.headers.get("X-BTW-Editor") != "1"
                    and request.headers.get("X-Requested-With") != "XMLHttpRequest"):
                who = audience(getattr(request, "user", None))
                if who:
                    count_view(request.user, who, area_for(request.path))
        except Exception:
            pass
        return response


def count_view(user, who, area):
    from .models import UsageAreaDay
    day = timezone.localtime(timezone.now(), EASTERN).date()
    if UsageAreaDay.objects.filter(user=user, day=day, area=area).update(views=F("views") + 1):
        return
    try:
        with transaction.atomic():
            UsageAreaDay.objects.create(user=user, audience=who, day=day, area=area, views=1)
    except IntegrityError:
        UsageAreaDay.objects.filter(user=user, day=day, area=area).update(views=F("views") + 1)


def tidy():
    from .models import UsageAreaDay, UsageSignIn
    cutoff = timezone.now() - timedelta(days=KEEP_DAYS)
    UsageSignIn.objects.filter(at__lt=cutoff).delete()
    UsageAreaDay.objects.filter(day__lt=cutoff.date()).delete()


def report(who, days):
    from django.contrib.auth import get_user_model
    from .models import UsageAreaDay, UsageSignIn
    since = timezone.now() - timedelta(days=days)
    since_day = timezone.localtime(since, EASTERN).date()
    sign_ins = UsageSignIn.objects.filter(audience=who, at__gte=since)
    visits = UsageAreaDay.objects.filter(audience=who, day__gte=since_day)
    per_person = {}
    for row in sign_ins.values("user").annotate(n=Count("pk"), last=Max("at")):
        per_person.setdefault(row["user"], {}).update(sign_ins=row["n"], last_sign_in=row["last"])
    for row in visits.values("user").annotate(pages=Sum("views"), days=Count("day", distinct=True), last=Max("day")):
        per_person.setdefault(row["user"], {}).update(pages=row["pages"], days=row["days"], last_day=row["last"])
    top = {}
    for row in visits.values("user", "area").annotate(n=Sum("views")).order_by("user", "-n"):
        top.setdefault(row["user"], [])
        if len(top[row["user"]]) < 3:
            top[row["user"]].append(row["area"])
    users = {u.pk: u for u in get_user_model().objects.filter(pk__in=per_person)}
    people = []
    for pk, stats in per_person.items():
        u = users.get(pk)
        if not u:
            continue
        name = u.get_full_name().strip() or getattr(getattr(u, "participant_account", None), "login_email", "") or u.username
        last = max([d for d in (stats.get("last_sign_in") and timezone.localtime(stats["last_sign_in"], EASTERN).date(), stats.get("last_day")) if d], default=None)
        people.append({"name": name, "sign_ins": stats.get("sign_ins", 0), "days": stats.get("days", 0),
                       "pages": stats.get("pages", 0), "last": last, "areas": top.get(pk, [])})
    people.sort(key=lambda p: (-(p["days"]), -p["sign_ins"], p["name"].lower()))
    areas = list(visits.values("area").annotate(pages=Sum("views"), people=Count("user", distinct=True)).order_by("-pages"))
    return {"sign_ins": sign_ins.count(), "active": len(people), "pages": sum(p["pages"] for p in people),
            "people": people, "areas": areas}
