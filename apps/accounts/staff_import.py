"""Settings → Staff → Import from base44: staff profiles from a CSV export (no sign-ins, no passwords)."""
import csv
import io
import re

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.staffing.models import StaffProfile

from .staff_views import account_manager, page

SESSION_KEY = "staff_import_rows"
SIZES = {"XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"}
SIZE_ALIASES = {"XXL": "2XL", "XXXL": "3XL", "SMALL": "S", "MEDIUM": "M", "LARGE": "L", "X-LARGE": "XL"}


def _digits(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else ""


def _phone(value):
    d = _digits(value)
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if d else (value or "").strip()


def _size(value):
    v = (value or "").strip().upper()
    v = SIZE_ALIASES.get(v, v)
    return v if v in SIZES else ""


def parse(raw):
    """Rows with what would happen to each."""
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    if not {"name", "email"} <= headers:
        raise ValueError("This doesn't look like the base44 staff export (it needs name and email columns).")
    existing = list(StaffProfile.objects.all())
    by_email = {p.email.lower(): p for p in existing if p.email}
    by_phone = {_digits(p.phone): p for p in existing if _digits(p.phone)}
    raw_rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]
    # Active people first, newest first, so a current record wins over an older copy of the same person.
    raw_rows.sort(key=lambda r: r.get("updated_date", ""), reverse=True)
    raw_rows.sort(key=lambda r: r.get("active", "true").lower() == "false")
    rows, seen_emails, seen_phones = [], set(), set()
    for r in raw_rows:
        name = r.get("name", "")
        parts = name.split()
        first, last = (parts[0], " ".join(parts[1:])) if parts else ("", "")
        email = r.get("email", "").lower()
        row = {"first_name": first[:100], "last_name": last[:100], "email": email, "phone": _phone(r.get("phone", "")),
               "street_address": r.get("mailing_street", "")[:200], "city": r.get("mailing_city", "")[:100],
               "state": r.get("mailing_state", "")[:100], "zip_code": r.get("mailing_zip", "")[:24],
               "shirt_size": _size(r.get("shirt_size")), "is_active": r.get("active", "true").lower() != "false",
               "base44_role": r.get("role", ""), "action": "add", "why": ""}
        phone = _digits(row["phone"])
        match = (by_email.get(email) if email else None) or (by_phone.get(phone) if phone else None)
        if r.get("is_sample", "").lower() == "true":
            row["action"], row["why"] = "skip", "Marked as sample in base44"
        elif not first:
            row["action"], row["why"] = "skip", "No name"
        elif first.lower() == "test":
            row["action"], row["why"] = "skip", "Test record"
        elif (email and email in seen_emails) or (phone and phone in seen_phones):
            row["action"], row["why"] = "skip", "Same person listed again (older or inactive copy)"
        elif match:
            row["action"], row["why"] = "skip", f"Already here as {match.first_name} {match.last_name}"
        if row["action"] == "add":
            if email:
                seen_emails.add(email)
            if phone:
                seen_phones.add(phone)
        rows.append(row)
    return rows


@account_manager
@require_http_methods(["GET", "POST"])
def import_staff(request):
    rows = request.session.get(SESSION_KEY)
    if request.method == "POST" and request.POST.get("action") == "check":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Choose the staff CSV file.")
            return redirect("accounts:staff-import")
        try:
            rows = parse(upload.read())
        except (ValueError, csv.Error) as exc:
            messages.error(request, str(exc))
            return redirect("accounts:staff-import")
        request.session[SESSION_KEY] = rows
        return redirect("accounts:staff-import")
    if request.method == "POST" and request.POST.get("action") == "cancel":
        request.session.pop(SESSION_KEY, None)
        return redirect("accounts:staff-import")
    if request.method == "POST" and request.POST.get("action") == "save" and rows:
        stamp = timezone.localdate().strftime("%b %d, %Y")
        made = 0
        with transaction.atomic():
            for r in rows:
                if r["action"] != "add":
                    continue
                note = f"Imported from base44 on {stamp}." + (f" base44 role: {r['base44_role']}." if r["base44_role"] else "")
                StaffProfile.objects.create(**{k: r[k] for k in ("first_name", "last_name", "email", "phone", "street_address",
                                                                 "city", "state", "zip_code", "shirt_size", "is_active")},
                                            internal_notes=note)
                made += 1
            from apps.core.audit import record_audit
            record_audit(actor=request.user, action="staff.imported", instance=request.user, source="staff_import",
                         new_data={"added": made}, reason="Staff profiles imported from a base44 export.")
        request.session.pop(SESSION_KEY, None)
        messages.success(request, f"{made} staff profile{'s' if made != 1 else ''} added. Give sign-ins to the people who need one from their profile.")
        return redirect("staffing:directory")
    counts = {"add": sum(1 for r in rows or [] if r["action"] == "add"), "skip": sum(1 for r in rows or [] if r["action"] == "skip")}
    return page(request, "accounts/staff_import.html", page_title="Import staff", rows=rows, counts=counts,
                back_url=reverse("staffing:directory"))
