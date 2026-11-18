"""Events → Import from base44: past and upcoming events from a base44 event export (CSV).

Each row is checked first: test events, samples and events already here are skipped. Staffing needs (per day),
the sign-up deadline, housing and the on-site contact come along. Housing notes that look like door codes or
passwords are left out unless the person importing ticks the box to keep them.
"""
import csv
import io
import json
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import Event

SESSION_KEY = "event_import_rows"
EASTERN = ZoneInfo("America/New_York")
TYPES = {t.value for t in Event.EventType}
STATUSES = {"upcoming": "upcoming", "completed": "completed", "cancelled": "cancelled", "canceled": "cancelled",
            "archived": "archived", "draft": "draft", "planning": "draft"}
SECRET = re.compile(r"(entry code|door code|lock ?box|gate code|wi-?fi|password|passcode|pin\b)", re.I)
HOUSING = {"hotel": "Hotel", "airbnb": "Airbnb", "house": "House", "camping": "Camping", "none": ""}


def _day(value):
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def _time(value):
    try:
        return time.fromisoformat((value or "").strip()[:5]) if (value or "").strip() else None
    except ValueError:
        return None


def _when(day, clock, fallback):
    return datetime.combine(day, clock or fallback, EASTERN)


def _coord(value, limit):
    try:
        number = Decimal((value or "").strip()).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError):
        return None
    return str(number) if -limit <= number <= limit else None


def _int(value):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _clean(value):
    value = (value or "").strip()
    return "" if value.upper() in ("N/A", "NA", "NONE", "TBD") else value


def _close(key, existing):
    """Same start date and nearly the same name (base44 typos like "Challenege")."""
    from difflib import SequenceMatcher
    name, day = key
    letters = re.sub(r"[^a-z0-9]", "", name)
    return any(day == other_day and SequenceMatcher(None, letters, re.sub(r"[^a-z0-9]", "", other)).ratio() >= 0.9
               for other, other_day in existing)


def parse(raw):
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not {"name", "start_date"} <= headers:
        raise ValueError("This doesn't look like the base44 event export (it needs name and start_date columns).")
    existing = {(e.name.strip().lower(), timezone.localtime(e.start_at, EASTERN).date()) for e in Event.objects.only("name", "start_at")}
    rows, seen = [], set()
    for r in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        name = r.get("name", "")
        start_day, end_day = _day(r.get("start_date")), _day(r.get("end_date"))
        end_day = end_day if end_day and start_day and end_day >= start_day else start_day
        kind = r.get("event_type", "").lower()
        housing_kind = HOUSING.get(r.get("housing_type", "").lower(), r.get("housing_type", "").title())
        housing_lines = [x for x in (housing_kind, _clean(r.get("hotel_name")), _clean(r.get("housing_address")),
                                     " ".join(x for x in (_clean(r.get("housing_city")), _clean(r.get("housing_state")),
                                                          _clean(r.get("housing_zip"))) if x)) if x]
        note = r.get("housing_info", "")
        try:
            daily = json.loads(r.get("daily_needs") or "[]")
        except ValueError:
            daily = []
        daily = [{"date": d.get("date"), "staff": _int(d.get("staff_needed")), "drivers": _int(d.get("drivers_needed"))}
                 for d in daily if isinstance(d, dict) and _day(d.get("date"))]
        deadline = r.get("staff_signup_deadline", "")
        row = {
            "name": name[:200], "venue": _clean(r.get("venue_name"))[:200], "address": _clean(r.get("address"))[:200],
            "city": _clean(r.get("city"))[:100], "state": _clean(r.get("state"))[:100], "zip": _clean(r.get("zip"))[:24],
            "event_type": kind if kind in TYPES else ("other" if kind else ""),
            "event_type_other": (r.get("event_type_custom", "") or ("" if kind in TYPES else kind))[:100],
            "start_day": start_day.isoformat() if start_day else "", "end_day": end_day.isoformat() if end_day else "",
            "start_time": r.get("start_time", ""), "end_time": r.get("end_time", ""),
            "setup_date": r.get("setup_date", "")[:10], "arrival_time": r.get("setup_arrival_time", ""),
            "return_date": r.get("arrival_home_date", "")[:10],
            "staff": _int(r.get("staff_needed")), "drivers": _int(r.get("drivers_needed")), "daily": daily,
            "deadline": deadline,
            "housing": "\n".join(housing_lines), "housing_note": note, "secret": bool(note and SECRET.search(note)),
            "contact_name": " ".join(r.get("contact_name", "").split())[:160], "contact_email": r.get("contact_email", "")[:254],
            "contact_phone": r.get("contact_phone", "").lstrip("'")[:40], "notes": r.get("notes", ""),
            "status": STATUSES.get(r.get("status", "").lower(), "draft"), "finalized": r.get("readiness", "").lower() == "finalized",
            "lat": _coord(r.get("latitude"), 90), "lng": _coord(r.get("longitude"), 180),
            "base44_id": r.get("id", ""), "action": "add", "why": "",
        }
        labels = dict(Event.EventType.choices)
        row["type_label"] = row["event_type_other"] if row["event_type"] == "other" and row["event_type_other"] else labels.get(row["event_type"], "")
        fmt = lambda d: f"{d:%b} {d.day}, {d.year}"
        row["dates_label"] = "" if not start_day else (fmt(start_day) if end_day == start_day else
                                                       (f"{start_day:%b} {start_day.day} – {fmt(end_day)}" if start_day.year == end_day.year else f"{fmt(start_day)} – {fmt(end_day)}"))
        key = (name.strip().lower(), start_day)
        if r.get("is_sample", "").lower() == "true":
            row["action"], row["why"] = "skip", "Marked as sample in base44"
        elif not name or name.lower().startswith("test"):
            row["action"], row["why"] = "skip", "Test event"
        elif not start_day:
            row["action"], row["why"] = "skip", "No start date"
        elif key in existing or _close(key, existing):
            row["action"], row["why"] = "skip", "Already here"
        elif key in seen:
            row["action"], row["why"] = "skip", "Listed twice"
        if row["action"] == "add":
            seen.add(key)
        rows.append(row)
    rows.sort(key=lambda r: r["start_day"] or "", reverse=True)
    return rows


def _save(row, keep_secret_notes):
    from apps.staffing.models import DailyStaffingNeed, StaffingPlan
    start_day, end_day = date.fromisoformat(row["start_day"]), date.fromisoformat(row["end_day"])
    start_clock, end_clock = _time(row["start_time"]), _time(row["end_time"])
    housing = row["housing"]
    if row["housing_note"] and (keep_secret_notes or not row["secret"]):
        housing = (housing + "\n\n" if housing else "") + row["housing_note"]
    event = Event(
        name=row["name"], start_at=_when(start_day, start_clock, time(8)), end_at=_when(end_day, end_clock, time(17)),
        location_name=row["venue"], address_line_1=row["address"], city=row["city"], state=row["state"], postal_code=row["zip"],
        country="US" if row["state"] else "", map_latitude=row["lat"], map_longitude=row["lng"], status=row["status"],
        event_type=row["event_type"], event_type_other=row["event_type_other"] if row["event_type"] == "other" else "",
        setup_date=_day(row["setup_date"]), staff_arrival_time=_time(row["arrival_time"]),
        daily_start_time=start_clock, daily_end_time=end_clock, return_date=_day(row["return_date"]),
        contact_name=row["contact_name"], contact_email=row["contact_email"], contact_phone=row["contact_phone"],
        housing_information=housing, description=row["notes"])
    if event.end_at < event.start_at:
        event.end_at = event.start_at
    event.full_clean(exclude=["code", "contact_email"])
    event.save()
    deadline = None
    if row["deadline"]:
        try:
            deadline = datetime.fromisoformat(row["deadline"][:16]).replace(tzinfo=EASTERN)
        except ValueError:
            deadline = None
    if row["staff"] or row["drivers"] or row["daily"]:
        plan = StaffingPlan.objects.create(event=event, required_staff=row["staff"], required_drivers=row["drivers"],
                                           signup_deadline=deadline)
        for need in row["daily"]:
            DailyStaffingNeed.objects.create(plan=plan, date=_day(need["date"]), required_staff=need["staff"],
                                             required_drivers=need["drivers"])
    return event


@login_required(login_url="/accounts/login/")
@require_http_methods(["GET", "POST"])
def import_events(request):
    from apps.accounts.roles import manager
    if not (manager(request.user) and request.user.has_perm("events.add_event")):
        raise PermissionDenied("Importing events is for Admins and Managers.")
    rows = request.session.get(SESSION_KEY)
    action = request.POST.get("action") if request.method == "POST" else None
    if action == "check":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Choose the event CSV file.")
            return redirect("events:import-base44")
        try:
            request.session[SESSION_KEY] = parse(upload.read())
        except (ValueError, csv.Error) as exc:
            messages.error(request, str(exc))
        return redirect("events:import-base44")
    if action == "cancel":
        request.session.pop(SESSION_KEY, None)
        return redirect("events:import-base44")
    if action == "save" and rows:
        keep = request.POST.get("keep_secret_notes") == "1"
        made = 0
        with transaction.atomic():
            for row in rows:
                if row["action"] == "add":
                    _save(row, keep)
                    made += 1
            from apps.core.audit import record_audit
            record_audit(actor=request.user, action="events.imported", instance=request.user, source="event_import",
                         new_data={"added": made}, reason="Events imported from a base44 export.")
        request.session.pop(SESSION_KEY, None)
        messages.success(request, f"{made} event{'s' if made != 1 else ''} added.")
        return redirect("events:event_list")
    counts = {"add": sum(1 for r in rows or [] if r["action"] == "add"),
              "skip": sum(1 for r in rows or [] if r["action"] == "skip"),
              "secret": sum(1 for r in rows or [] if r["action"] == "add" and r["secret"])}
    return render(request, "events/base44_import.html", {"page_title": "Import events", "rows": rows, "counts": counts,
                                                          "back_url": reverse("events:event_list")})
