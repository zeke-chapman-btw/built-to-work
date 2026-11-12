"""MY STUFF: one place for a staff member's own schedule, days worked and pay."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.roles import active_staff, has_role
from apps.internal_ui.views import _staff_context
from .general_views import published_events
from .models import StaffProfile


def my_tabs(request, active):
    """Tabs shared by My schedule, Days worked and My pay."""
    profile = StaffProfile.objects.filter(user=request.user, is_active=True).first() if active_staff(request.user) else None
    tabs = [dict(key="schedule", label="My schedule", url=reverse("my:schedule")),
            dict(key="workdays", label="Days worked", url=reverse("workdays:mine"))]
    if profile and profile.my_pay_enabled:
        tabs.append(dict(key="pay", label="My pay", url=reverse("reporting:my-pay")))
    for tab in tabs:
        tab["active"] = tab["key"] == active
    return tabs


def _stay(event):
    return [dict(name=s.display_name, kind=s.get_kind_display(), address=", ".join(v for v in (s.address_line_1, s.city, s.state) if v),
                 map_query=s.map_query, phone=s.phone, check_in=s.check_in, check_out=s.check_out,
                 confirmation=s.confirmation_number, rooms=s.rooms, notes=s.notes) for s in event.lodgings.all()]


def _event(event):
    return dict(id=event.pk, name=event.name, start=event.local_start_at, end=event.local_end_at,
                venue=event.location_name, city=event.city, state=event.state,
                address=", ".join(v for v in (event.address_line_1, event.city, event.state) if v),
                setup_date=event.setup_date, arrival=event.staff_arrival_time, return_date=event.return_date,
                url=reverse("staffing:staff-event", args=[event.pk]))


@login_required(login_url="/admin/login/")
@require_GET
def schedule(request):
    if not active_staff(request.user):
        raise PermissionDenied
    profile = StaffProfile.objects.filter(user=request.user, is_active=True).first()
    from apps.accounts.operational_access import safe_event_worker
    general_only = safe_event_worker(request.user)  # same policy as the general event page
    now = timezone.now()
    upcoming, past, requests, open_events = [], [], [], []
    if profile:
        assigned_ids = set()
        rows = (profile.assignments.filter(status="confirmed").exclude(event__status__in=("draft", "cancelled"))
                .select_related("event").prefetch_related("days", "event__lodgings").order_by("event__start_at"))
        for row in rows:
            event = row.event
            assigned_ids.add(event.pk)
            item = dict(_event(event), role=row.role or row.get_coverage_role_display(),
                        days=[d.date for d in row.days.all()],
                        checkin_url=reverse("workdays:checkin", args=[event.pk]) if has_role(request.user, "event_staff") else "",
                        housing=[] if general_only else _stay(event), travel="" if general_only else event.logistics_instructions,
                        contact={} if general_only else dict(name=event.contact_name, phone=event.contact_phone, email=event.contact_email))
            if general_only:
                item.update(address="", setup_date=None, arrival=None, return_date=None)
            (upcoming if event.end_at >= now else past).append(item)
        past = list(reversed(past))[:10]
        for req in profile.availability_requests.select_related("event").order_by("-created_at")[:20]:
            if req.event.status in ("draft",):
                continue
            if req.status == "approved" and req.event_id in assigned_ids:
                continue
            requests.append(dict(_event(req.event), status=req.get_status_display(), status_key=req.status,
                                 dates=req.requested_dates))
        pending_ids = {r["id"] for r in requests if r["status_key"] in ("pending", "approved")}
        for event in (published_events().filter(status="upcoming", end_at__gt=now, staffing_plan__is_published=True)
                      .exclude(pk__in=assigned_ids | pending_ids).order_by("start_at")[:20]):
            open_events.append(_event(event))
    response = render(request, "staffing/my_schedule.html", _staff_context(request, page_title="My stuff",
        my_tabs=my_tabs(request, "schedule"), profile=profile, upcoming=upcoming, past=past, requests=requests, open_events=open_events))
    response["Cache-Control"] = "private, no-store"
    return response
