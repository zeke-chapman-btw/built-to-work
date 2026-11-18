"""Reports → Business: money in, what customers paid for vs got, the participant path, events."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import manager

RANGES = (("month", "This month"), ("quarter", "This quarter"), ("year", "This year"), ("12m", "Last 12 months"), ("all", "All time"))


def window(key, today):
    if key == "month":
        return today.replace(day=1), today
    if key == "quarter":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
    if key == "12m":
        return today - timedelta(days=365), today
    if key == "all":
        return date(2000, 1, 1), today
    return date(today.year, 1, 1), today


def _money(cents):
    return f"${Decimal(cents or 0) / 100:,.0f}"


def _dt(day, end=False):
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    return datetime.combine(day + timedelta(days=1) if end else day, time(0), ZoneInfo("America/New_York"))


def revenue(start, end):
    from .models import MonthlyAgreement, PositionCharge
    charges = PositionCharge.objects.filter(created_at__gte=_dt(start), created_at__lt=_dt(end, True))
    paid = charges.filter(status="paid").aggregate(t=Sum("amount_cents"))["t"] or 0
    pending = charges.filter(status__in=["processing", "waiting", "not_set_up"]).aggregate(t=Sum("amount_cents"))["t"] or 0
    failed = charges.filter(status="failed").aggregate(t=Sum("amount_cents"))["t"] or 0
    active = MonthlyAgreement.objects.filter(status__in=["active", "scheduled"])
    monthly = active.aggregate(t=Sum("amount_cents"))["t"] or 0
    ending = MonthlyAgreement.objects.filter(status="active", ends_on__lte=timezone.localdate() + timedelta(days=60)).count()
    return [
        {"label": "Position fees collected", "value": _money(paid), "detail": f"{charges.filter(status='paid').count()} paid"},
        {"label": "Position fees in progress", "value": _money(pending), "detail": "Processing or waiting on a bank account"},
        {"label": "Monthly recurring", "value": _money(monthly), "detail": f"{active.count()} active agreement{'s' if active.count() != 1 else ''}"},
        {"label": "Failed payments", "value": _money(failed), "detail": f"{ending} agreement{'s' if ending != 1 else ''} ending in 60 days", "warn": bool(failed)},
    ]


def customers(start, end):
    from apps.customers.models import CustomerOrganization
    from .delivery import progress
    from .models import MonthlyAgreement, PositionCharge
    rows = []
    orgs = CustomerOrganization.objects.exclude(status="archived").annotate(
        positions=Count("workforce_needs", filter=Q(workforce_needs__status="active"), distinct=True)).order_by("name")
    for org in orgs:
        charges = PositionCharge.objects.filter(organization=org)
        paid_total = charges.filter(status="paid", created_at__gte=_dt(start), created_at__lt=_dt(end, True)).aggregate(t=Sum("amount_cents"))["t"] or 0
        paid_for = delivered = hired = 0
        for need in org.workforce_needs.filter(charges__isnull=False).distinct().select_related("effective_revision"):
            p = progress(need)
            paid_for += p["paid_for"]
            delivered += p["delivered"] if p["unit"] != "people" else p["hired"]
            hired += p["hired"]
        monthly = MonthlyAgreement.objects.filter(organization=org, status__in=["active", "scheduled"]).aggregate(t=Sum("amount_cents"))["t"] or 0
        if not (org.positions or paid_for or paid_total or monthly):
            continue
        rows.append({"org": org, "positions": org.positions, "paid_for": paid_for, "delivered": delivered, "hired": hired,
                     "fees": _money(paid_total), "fees_cents": paid_total, "monthly": _money(monthly) if monthly else "",
                     "behind": paid_for and delivered < paid_for,
                     "url": reverse("customers:workspace", args=[org.pk, "billing"])})
    rows.sort(key=lambda r: (-r["fees_cents"], r["org"].name))
    return rows


def funnel(start, end):
    from apps.participants.models import Participant
    from apps.recruiting.models import CandidateDisclosure, CandidateOpportunity, EmployerCandidateFeedback
    s, e = _dt(start), _dt(end, True)
    steps = [
        ("Signed up", Participant.objects.filter(kind="person", created_at__gte=s, created_at__lt=e).count()),
        ("Became a candidate for a position", CandidateOpportunity.objects.filter(created_at__gte=s, created_at__lt=e).values("participant").distinct().count()),
        ("Shared with a customer", CandidateDisclosure.objects.filter(released_at__gte=s, released_at__lt=e).values("participant").distinct().count()),
        ("Interview requested", EmployerCandidateFeedback.objects.filter(kind="interview", created_at__gte=s, created_at__lt=e).values("disclosure__participant").distinct().count()),
        ("Hired", EmployerCandidateFeedback.objects.filter(kind="report_hired", created_at__gte=s, created_at__lt=e).values("disclosure__participant").distinct().count()),
    ]
    top = max([n for _l, n in steps] + [1])
    return [{"label": label, "count": n, "width": max(2, round(100 * n / top)) if n else 0} for label, n in steps]


def events(start, end):
    from apps.event_reports.models import ManualExpense
    from apps.events.models import Event
    from apps.workdays.models import Workday
    rows = []
    found = Event.objects.filter(start_at__gte=_dt(start), start_at__lt=_dt(end, True)).exclude(status__in=["cancelled", "draft"]) \
        .annotate(signups=Count("registrations", filter=Q(registrations__status="active"), distinct=True)).order_by("-start_at")[:40]
    for event in found:
        cost = ManualExpense.objects.filter(event=event, is_active=True, treatment="cost").aggregate(t=Sum("amount"))["t"] or 0
        pay = Workday.objects.filter(event=event, is_active=True, approval="approved").aggregate(
            t=Sum("pay_amount"), d=Sum("per_diem_amount"))
        staff_cost = (pay["t"] or 0) + (pay["d"] or 0)
        total = Decimal(cost) + Decimal(staff_cost)
        rows.append({"event": event, "signups": event.signups, "expenses": f"${cost:,.0f}", "staff": f"${staff_cost:,.0f}",
                     "total": f"${total:,.0f}", "per_person": f"${total / event.signups:,.0f}" if event.signups and total else "—",
                     "url": reverse("events:event_detail", args=[event.pk])})
    return rows


@login_required(login_url="/admin/login/")
def overview(request):
    if not manager(request.user):
        raise PermissionDenied("The business overview is for Admins and Managers.")
    key = request.GET.get("range") or "year"
    key = key if key in dict(RANGES) else "year"
    start, end = window(key, timezone.localdate())
    from apps.reporting.views import tabs_for
    try:
        tabs = tabs_for(request.user, "business")
    except Exception:
        tabs = []
    return render(request, "billing/business.html", {
        "page_title": "Reports & Analytics", "report_tabs": tabs, "range": key,
        "ranges": [{"key": k, "label": label, "active": k == key} for k, label in RANGES],
        "start": start, "end": end, "money": revenue(start, end), "customers": customers(start, end),
        "funnel": funnel(start, end), "events": events(start, end)})
