"""Events → Import from base44 → staff sign-ups and expenses.

Takes a small JSON file made from base44's EventStaffAssignment, EventExpense and Staff tables. Events are matched
by name and start date (import the events first), staff by email, phone or name. Nothing about pay comes across:
work days, pay stubs and payroll weeks stay in base44 until pay history is brought over in one go.
"""
import json
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .base44_import import _close
from .models import Event

SESSION_KEY = "event_history_import"
EASTERN = ZoneInfo("America/New_York")
ROLES = {"staff": "staff", "staff_lead": "staff_lead", "driver": "driver", "combined": "combined"}
SHIRT = {"XS": "XS", "S": "S", "M": "M", "L": "L", "XL": "XL", "2XL": "2XL", "XXL": "2XL", "3XL": "3XL", "XXXL": "3XL",
         "4XL": "4XL", "5XL": "5XL", "SMALL": "S", "MEDIUM": "M", "LARGE": "L", "X-LARGE": "XL"}


def _digits(value):
    return re.sub(r"\D", "", value or "")[-10:]


def _day(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _events():
    return [(e, e.name.strip().lower(), timezone.localtime(e.start_at, EASTERN).date()) for e in Event.objects.all()]


def find_event(ref, events):
    name, day = (ref or {}).get("name", "").strip().lower(), _day((ref or {}).get("start_date"))
    if not name or not day:
        return None
    for event, other, other_day in events:
        if other == name and other_day == day:
            return event
    for event, other, other_day in events:
        if _close((name, day), [(other, other_day)]):
            return event
    return None


def find_staff(person, profiles):
    from apps.staffing.models import StaffProfile
    email = (person.get("email") or "").strip().lower()
    phone = _digits(person.get("phone"))
    name = " ".join((person.get("name") or "").lower().split())
    for test in (lambda p: email and p.email.strip().lower() == email,
                 lambda p: len(phone) == 10 and _digits(p.phone) == phone,
                 lambda p: name and f"{p.first_name} {p.last_name}".lower().strip() == name):
        hit = [p for p in profiles if test(p)]
        if len(hit) == 1:
            return hit[0]
    return None


def check(data):
    """What would happen to each row, without saving anything."""
    from apps.event_reports.models import ManualExpense
    from apps.staffing.models import StaffAssignment, StaffProfile
    events = _events()
    profiles = list(StaffProfile.objects.all())
    out = {"assignments": [], "expenses": [], "staff": []}
    for row in data.get("assignments", []):
        event, staff = find_event(row.get("event"), events), find_staff(row.get("staff") or {}, profiles)
        item = {"event": (row.get("event") or {}).get("name", ""), "event_id": str(event.pk) if event else "",
                "staff": (row.get("staff") or {}).get("name", ""), "staff_id": str(staff.pk) if staff else "",
                "role": ROLES.get((row.get("role") or "").lower(), ""), "days": sorted(d for d in row.get("work_days") or [] if _day(d)),
                "status": (row.get("status") or "").lower(), "note": (row.get("notes") or "")[:500], "action": "add", "why": ""}
        if item["status"] != "confirmed":
            item["action"], item["why"] = "skip", "Not confirmed in base44"
        elif not event:
            item["action"], item["why"] = "skip", "Event isn't here yet"
        elif not staff:
            item["action"], item["why"] = "skip", "No matching staff member"
        elif StaffAssignment.objects.filter(event=event, staff=staff, status="confirmed").exists():
            item["action"], item["why"] = "skip", "Already assigned"
        elif not item["days"]:
            item["action"], item["why"] = "skip", "No work days"
        out["assignments"].append(item)
    for row in data.get("expenses", []):
        event = find_event(row.get("event"), events)
        try:
            amount = Decimal(str(row.get("amount"))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            amount = None
        day = _day(row.get("expense_date"))
        item = {"event": (row.get("event") or {}).get("name", ""), "event_id": str(event.pk) if event else "",
                "description": (row.get("description") or "").strip()[:240] or "Expense",
                "category": (row.get("category") or "other").replace("_", " ").title()[:120],
                "amount": str(amount) if amount is not None else "", "date": day.isoformat() if day else "",
                "notes": (row.get("notes") or "")[:2000], "by": (row.get("entered_by") or "")[:120], "action": "add", "why": ""}
        if (row.get("status") or "").lower() == "voided":
            item["action"], item["why"] = "skip", "Voided in base44"
        elif not event:
            item["action"], item["why"] = "skip", "Event isn't here yet"
        elif amount is None or amount < 0 or not day:
            item["action"], item["why"] = "skip", "Missing amount or date"
        elif ManualExpense.objects.filter(event=event, expense_date=day, amount=amount, description__iexact=item["description"]).exists():
            item["action"], item["why"] = "skip", "Already here"
        out["expenses"].append(item)
    for row in data.get("staff", []):
        staff = find_staff(row, profiles)
        fill = {}
        if staff:
            for ours, theirs in (("street_address", "mailing_street"), ("city", "mailing_city"), ("state", "mailing_state"),
                                 ("zip_code", "mailing_zip")):
                if not getattr(staff, ours) and (row.get(theirs) or "").strip():
                    fill[ours] = row[theirs].strip()[:200]
            size = SHIRT.get((row.get("shirt_size") or "").strip().upper())
            if size and not staff.shirt_size:
                fill["shirt_size"] = size
        if staff and fill:
            out["staff"].append({"staff": row.get("name", ""), "staff_id": str(staff.pk), "fill": fill})
    return out


def save(checked, actor):
    from apps.core.audit import record_audit
    from apps.event_reports.models import ExpenseCategory, ManualExpense
    from apps.staffing.calendar import day_window
    from apps.staffing.models import StaffProfile
    from apps.staffing.services import _create_assignment
    made = {"assignments": 0, "expenses": 0, "staff": 0, "problems": []}
    with transaction.atomic():
        for item in checked["assignments"]:
            if item["action"] != "add":
                continue
            event = Event.objects.get(pk=item["event_id"])
            staff = StaffProfile.objects.get(pk=item["staff_id"])
            try:
                days = [day_window(event, _day(d)) for d in item["days"]]
                with transaction.atomic():
                    _create_assignment(actor=actor, event=event, staff=staff, days=days, coverage_role=item["role"],
                                       reason="Brought over from base44." + (f" Note: {item['note']}" if item["note"] else ""))
                made["assignments"] += 1
            except ValidationError as exc:
                made["problems"].append(f"{item['staff']} · {item['event']}: {' '.join(exc.messages)}")
        for item in checked["expenses"]:
            if item["action"] != "add":
                continue
            category = (ExpenseCategory.objects.filter(name__iexact=item["category"]).first()
                        or ExpenseCategory.objects.create(name=item["category"]))
            note = item["notes"] + (("\n" if item["notes"] else "") + f"Entered in base44 by {item['by']}" if item["by"] else "")
            ManualExpense.objects.create(event_id=item["event_id"], description=item["description"], notes=note,
                                         expense_date=_day(item["date"]), category=category, category_name=category.name,
                                         amount=Decimal(item["amount"]), created_by=actor, edited_by=actor,
                                         reason="Brought over from base44.", reference="base44", request_key=uuid.uuid4())
            made["expenses"] += 1
        for item in checked["staff"]:
            staff = StaffProfile.objects.get(pk=item["staff_id"])
            changed = {k: v for k, v in item["fill"].items() if not getattr(staff, k)}
            if changed:
                for k, v in changed.items():
                    setattr(staff, k, v)
                staff.save(update_fields=list(changed) + ["updated_at"])
                made["staff"] += 1
        record_audit(actor=actor, action="events.history_imported", instance=actor, source="event_import",
                     new_data={k: v for k, v in made.items() if k != "problems"}, reason="Staff sign-ups and expenses brought over from base44.")
    return made


@login_required(login_url="/accounts/login/")
@require_http_methods(["GET", "POST"])
def import_history(request):
    if not (request.user.is_active and request.user.is_superuser):
        raise PermissionDenied("Bringing over base44 history is for administrators.")
    checked = request.session.get(SESSION_KEY)
    action = request.POST.get("action") if request.method == "POST" else None
    if action == "check":
        upload = request.FILES.get("file")
        try:
            data = json.loads(upload.read().decode("utf-8-sig")) if upload else None
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            messages.error(request, "Choose the base44 history file (.json).")
            return redirect("events:import-base44-history")
        request.session[SESSION_KEY] = check(data)
        return redirect("events:import-base44-history")
    if action == "cancel":
        request.session.pop(SESSION_KEY, None)
        return redirect("events:import-base44-history")
    if action == "save" and checked:
        made = save(checked, request.user)
        request.session.pop(SESSION_KEY, None)
        messages.success(request, f"Added {made['assignments']} staff assignment(s) and {made['expenses']} expense(s); "
                                  f"filled in details for {made['staff']} staff member(s).")
        for problem in made["problems"][:10]:
            messages.warning(request, problem)
        return redirect("events:import-base44-history")
    counts = {}
    if checked:
        for key in ("assignments", "expenses"):
            counts[key] = {"add": sum(1 for r in checked[key] if r["action"] == "add"),
                           "skip": sum(1 for r in checked[key] if r["action"] != "add")}
        counts["staff"] = len(checked["staff"])
    return render(request, "events/base44_history.html", {"page_title": "Import base44 history", "checked": checked,
                                                          "counts": counts, "back_url": reverse("events:import-base44")})
