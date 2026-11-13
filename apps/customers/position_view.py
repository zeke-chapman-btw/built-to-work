"""Turn a position (hiring revision) into quick-glance tiles and grouped cards for the staff position page."""
from decimal import Decimal

NO_ANSWER = {"none", "not_applicable", ""}
BASIS = {"hour": "hr", "day": "day", "week": "week", "month": "month", "year": "year", "project": "project"}
YES_NO_TONE = {"yes": "success", "no": "neutral", "not_applicable": "neutral"}


def _money(value):
    if value is None or value == "":
        return ""
    value = Decimal(str(value))
    return f"${value:,.0f}" if value == value.to_integral() else f"${value:,.2f}"


def _display(rev, name):
    getter = getattr(rev, f"get_{name}_display", None)
    return getter() if getter else getattr(rev, name)


def _row(label, value=None, chip=None, tone="neutral"):
    return {"label": label, "value": value or "", "chip": chip or "", "tone": tone}


def _answer(rev, label, name):
    """A provided/none answer: show the details when given, otherwise a dash."""
    response = getattr(rev, f"{name}_response", "") or ""
    details = getattr(rev, f"{name}_details", "") or ""
    if response in NO_ANSWER:
        return _row(label)
    return _row(label, details or _display(rev, f"{name}_response"))


def _yes_no(rev, label, name):
    response = getattr(rev, f"{name}_response", "") or ""
    details = getattr(rev, f"{name}_details", "") or ""
    if not response:
        return _row(label)
    chip = {"yes": "Yes", "no": "No", "not_applicable": "N/A"}.get(response, _display(rev, f"{name}_response"))
    return _row(label, details, chip=chip, tone=YES_NO_TONE.get(response, "neutral"))


def pay_text(rev):
    low, high = _money(rev.pay_minimum), _money(rev.pay_maximum)
    if rev.pay_response != "provided" or not (low or high):
        return rev.pay_details or ""
    amount = f"{low}–{high}" if low and high and low != high else (low or high)
    basis = BASIS.get(rev.pay_basis)
    return f"{amount} / {basis}" if basis else amount


def hours_text(rev):
    if rev.hours_mode == "specified" and rev.hours_per_week:
        hours = rev.hours_per_week
        hours = int(hours) if Decimal(str(hours)) == Decimal(str(hours)).to_integral() else hours
        return f"{hours} hrs/week"
    return rev.schedule_details if rev.hours_mode == "variable" else ""


def start_text(rev):
    if rev.start_mode == "date" and rev.start_date:
        return rev.start_date.strftime("%b %-d, %Y")
    return rev.start_timeframe or ""


def position_view(rev):
    if not rev:
        return None
    shift = _display(rev, "shift") if rev.shift else ""
    schedule = " · ".join(v for v in (shift, hours_text(rev)) if v)
    tiles = [
        {"label": "Workers needed", "value": rev.worker_quantity or ""},
        {"label": "Pay", "value": pay_text(rev)},
        {"label": "Schedule", "value": schedule},
        {"label": "Start", "value": start_text(rev)},
    ]
    address = ", ".join(v for v in (rev.address_line1, rev.address_line2, rev.city,
                                     " ".join(v for v in (rev.location_state, rev.postal_code) if v)) if v)
    location_name = rev.location_name or (str(rev.location) if getattr(rev, "location_id", None) else "")
    duration = (_row("Length", "", chip="Ongoing", tone="info") if rev.duration_response == "ongoing"
                else _row("Length", rev.duration_details or (rev.end_date.strftime("Until %b %-d, %Y") if rev.end_date else "")))
    def exp(name, label):
        row = _answer(rev, label, name)
        years = getattr(rev, f"{name}_years", None)
        if years and getattr(rev, f"{name}_response", "") == "provided":
            row["value"] = f"{years}+ years" + (f" · {row['value']}" if row["value"] and row["value"] != "Provided" else "")
        return row
    per_diem = _answer(rev, "Per diem", "per_diem")
    if rev.per_diem_response == "provided" and rev.per_diem_amount:
        per_diem["value"] = _money(rev.per_diem_amount) + (f" · {rev.per_diem_details}" if rev.per_diem_details else "")
    cards = [
        {"key": "role", "title": "Role", "rows": [
            _row("Trade", rev.trade),
            _row("Type", "", chip=_display(rev, "employment_type") if rev.employment_type else "", tone="info"),
            _row("Workers", str(rev.worker_quantity) if rev.worker_quantity else ""),
            _row("Description", rev.description),
            _row("Notes", rev.employment_details),
        ]},
        {"key": "location", "title": "Location", "rows": [
            _row("Setting", "", chip=_display(rev, "location_mode") if rev.location_mode else "", tone="neutral"),
            _row("Place", location_name),
            _row("Address", address),
            _row("Notes", rev.location_details),
        ]},
        {"key": "pay", "title": "Pay & benefits", "rows": [
            _row("Pay", pay_text(rev)),
            _yes_no(rev, "Overtime", "overtime"),
            _answer(rev, "Benefits", "benefits"),
            per_diem,
            _row("Notes", rev.pay_details if rev.pay_response == "provided" else ""),
        ]},
        {"key": "schedule", "title": "Schedule", "rows": [
            _row("Shift", "", chip=shift, tone="neutral"),
            _row("Hours", hours_text(rev)),
            _row("Days & times", rev.schedule_details if rev.hours_mode != "variable" else ""),
            _row("Start", start_text(rev)),
            duration,
        ]},
        {"key": "requirements", "title": "Requirements", "rows": [
            _answer(rev, "Skills needed", "required_skills"),
            _answer(rev, "Skills preferred", "preferred_skills"),
            exp("required_experience", "Experience needed"),
            exp("preferred_experience", "Experience preferred"),
            _answer(rev, "Licenses needed", "required_credentials"),
            _answer(rev, "Licenses preferred", "preferred_credentials"),
        ]},
        {"key": "environment", "title": "Work environment", "rows": [
            _yes_no(rev, "Travel", "travel"),
            _yes_no(rev, "Overnights", "overnight"),
            _answer(rev, "Conditions", "conditions"),
            _answer(rev, "Equipment", "equipment"),
        ]},
        {"key": "contact", "title": "Contact & hiring", "rows": [
            _row("Contact", rev.contact_name),
            _row("Email", rev.contact_email),
            _row("Phone", rev.contact_phone),
            _answer(rev, "Screening", "screening"),
            _row("Hiring timeline", rev.hiring_timeline),
            _row("Decide by", rev.hiring_deadline.strftime("%b %-d, %Y") if rev.hiring_deadline else ""),
            _answer(rev, "Other", "other"),
        ]},
    ]
    optional = {"Notes", "Other", "Decide by"}
    for card in cards:
        card["rows"] = [r for r in card["rows"] if r["value"] or r["chip"] or r["label"] not in optional]
    return {"tiles": tiles, "cards": cards}
