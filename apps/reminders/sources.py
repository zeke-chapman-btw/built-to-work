"""What reminders can watch. Each source lists the open items for today.

A new kind of reminder only needs code when its data isn't one of these. Each
source says which dates it can fire on (``modes``), which related people it
knows (``related``) and which fill-ins its wording can use (``fields``).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    user: object = None
    email: str = ""
    phone: str = ""
    participant: object = None
    portal_url: str = ""  # customers get their own portal link, never a staff page

    @property
    def first_name(self):
        return (self.name or "").split(" ")[0]


@dataclass
class Item:
    ref: str
    line: str
    link: str = ""
    anchor: date | None = None
    created: datetime | None = None
    fields: dict = field(default_factory=dict)
    related: dict = field(default_factory=dict)
    fingerprint: str = ""
    facts: dict = field(default_factory=dict)  # what conditions are checked against


def user_target(user):
    if not user or not user.is_active:
        return None
    try:
        profile = user.staff_profile
    except Exception:
        profile = None
    return Target(f"u:{user.pk}", user.get_full_name() or user.get_username(), user=user,
                  email=(profile.email if profile and profile.email else user.email) or "",
                  phone=(profile.phone if profile else "") or "")


def staff_target(profile):
    if not profile or not profile.is_active:
        return None
    user = profile.user if profile.user_id and profile.user.is_active else None
    return Target(f"u:{user.pk}" if user else f"s:{profile.pk}", f"{profile.first_name} {profile.last_name}".strip(),
                  user=user, email=profile.email or (user.email if user else ""), phone=profile.phone or "")


def participant_target(person):
    if not person:
        return None
    name = f"{person.preferred_name or person.first_name} {person.last_name}".strip()
    return Target(f"p:{person.pk}", name, email=person.contact_email or "", phone=person.contact_phone or "",
                  participant=person)


def _people(*values):
    return [v for v in values if v]


def _event_place(event):
    parts = [event.location_name, event.city]
    return ", ".join(p for p in parts if p) or "the event site"


def _fp(*values):
    return hashlib.sha256("|".join(str(v) for v in values).encode()).hexdigest()[:32]


def _local(value):
    return timezone.localtime(value, EASTERN) if value else None


def _when(value):
    value = _local(value)
    return f"{value:%a %b} {value.day} at {value:%I:%M %p}".replace(" 0", " ") if value else ""


# ---------------------------------------------------------------- Events

def _upcoming_events(today, days_ahead=21):
    from apps.events.models import Event
    start = datetime.combine(today - timedelta(days=1), datetime.min.time(), EASTERN)
    end = datetime.combine(today + timedelta(days=days_ahead), datetime.min.time(), EASTERN)
    return Event.objects.filter(status="upcoming").filter(
        Q(start_at__gte=start, start_at__lt=end) | Q(setup_date__gte=today, setup_date__lte=today + timedelta(days=days_ahead)))


def staff_working_soon(today, now):
    from apps.staffing.models import StaffAssignment
    rows = (StaffAssignment.objects.filter(status="confirmed", event__in=_upcoming_events(today))
            .select_related("event", "staff", "staff__user").prefetch_related("days"))
    for row in rows:
        event = row.event
        days = list(row.days.all())
        first = days[0] if days else None
        first_day = first.date if first else event.first_day
        arrival = (first.arrival_at or first.starts_at) if first else None
        when = _when(arrival) if arrival else f"{first_day:%a %b} {first_day.day}"
        target = staff_target(row.staff)
        yield Item(ref=f"assignment:{row.pk}", anchor=first_day,
                   line=f"{event.name} · {when}", link=reverse("events:event_detail", args=[event.pk]),
                   fields={"first_name": row.staff.first_name, "event": event.name, "when": when,
                           "place": _event_place(event)},
                   related={"staff": _people(target)})


def event_changed(today, now):
    from apps.staffing.models import StaffAssignment
    events = _upcoming_events(today, days_ahead=90)
    staff = {}
    for row in StaffAssignment.objects.filter(status="confirmed", event__in=events).select_related("staff", "staff__user"):
        staff.setdefault(row.event_id, []).append(staff_target(row.staff))
    for event in events.filter(pk__in=list(staff)):
        when = _when(event.start_at)
        yield Item(ref=f"event:{event.pk}", anchor=event.first_day,
                   line=f"{event.name} · now {when} at {_event_place(event)}",
                   link=reverse("events:event_detail", args=[event.pk]),
                   fingerprint=_fp(event.start_at, event.end_at, event.setup_date, event.staff_arrival_time,
                                   event.location_name, event.address_line_1, event.city),
                   fields={"event": event.name, "when": when, "place": _event_place(event)},
                   related={"staff": _people(*staff[event.pk])})


# ---------------------------------------------------------- Participants

def participant_event_day_before(today, now):
    from apps.events.models import EventRegistration
    rows = (EventRegistration.objects.filter(status="active", ticket_sms_opt_in=True, event__in=_upcoming_events(today))
            .select_related("event", "participant"))
    for row in rows:
        event, person = row.event, row.participant
        when = _when(event.start_at)
        yield Item(ref=f"registration:{row.pk}", anchor=event.local_start_at.date(),
                   line=f"{person.first_name} {person.last_name} · {event.name}",
                   link=reverse("participants:staff-profile", args=[person.pk]),
                   fields={"first_name": person.preferred_name or person.first_name, "event": event.name,
                           "when": when, "place": _event_place(event)},
                   related={"participant": _people(participant_target(person))})


def follow_ups(today, now):
    from apps.crm.models import Interaction
    rows = (Interaction.objects.filter(follow_up_on__gte=today - timedelta(days=30), follow_up_on__lte=today + timedelta(days=30))
            .select_related("participant", "organization", "responsible_staff", "responsible_staff__user", "created_by"))
    for row in rows[:500]:
        who = (f"{row.participant.first_name} {row.participant.last_name}" if row.participant_id else
               str(row.organization) if row.organization_id else "a contact")
        link = (reverse("participants:staff-profile", args=[row.participant_id]) + "?section=activity" if row.participant_id else
                reverse("customers:workspace", args=[row.organization_id, "overview"]) if row.organization_id else "")
        owner = staff_target(row.responsible_staff) if row.responsible_staff_id else None
        yield Item(ref=f"interaction:{row.pk}", anchor=row.follow_up_on, line=f"Follow up with {who}: {row.summary}",
                   link=link, fields={"name": who, "summary": row.summary},
                   related={"owner": _people(owner or user_target(row.created_by))})


def unhandled_text_replies(today, now):
    from apps.messaging.models import TextMessage
    rows = (TextMessage.objects.filter(direction="in", state="new", created_at__gte=now - timedelta(days=60))
            .select_related("participant", "contact", "replies_to"))
    for row in rows[:500]:
        who = (f"{row.participant.first_name} {row.participant.last_name}" if row.participant_id else
               str(row.contact) if row.contact_id else f"(…{row.phone[-4:]})")
        yield Item(ref=f"text:{row.pk}", anchor=_local(row.created_at).date(), created=row.created_at,
                   line=f"Text from {who}: {row.body[:80]}", link=reverse("messaging:texts"),
                   fields={"name": who}, related={"owner": _people(user_target(row.replies_to))})


def missing_info(today, now):
    from apps.recruiting.models import ProfileUpdateRequest
    rows = (ProfileUpdateRequest.objects.filter(fulfilled_by__isnull=True, withdrawn_at__isnull=True,
                                                created_at__gte=now - timedelta(days=120))
            .select_related("participant", "requested_by"))
    for row in rows[:500]:
        person = row.participant
        yield Item(ref=f"update-request:{row.pk}", anchor=_local(row.created_at).date(), created=row.created_at,
                   line=f"{person.first_name} {person.last_name} hasn't sent what you asked for",
                   link=reverse("participants:staff-profile", args=[person.pk]),
                   fields={"name": f"{person.first_name} {person.last_name}"},
                   related={"owner": _people(user_target(row.requested_by))})


def new_interest(today, now):
    from apps.recruiting.models import CandidateInterest
    rows = (CandidateInterest.objects.filter(active=True, status="received", created_at__gte=now - timedelta(days=60))
            .select_related("participant", "opportunity__owner", "opportunity__need"))
    for row in rows[:500]:
        person = row.participant
        yield Item(ref=f"interest:{row.pk}", anchor=_local(row.created_at).date(), created=row.created_at,
                   line=f"{person.first_name} {person.last_name} is interested in a position",
                   link=reverse("recruiting:position", args=[row.opportunity_id]),
                   fields={"name": f"{person.first_name} {person.last_name}"},
                   related={"recruiter": _people(user_target(row.opportunity.owner))})


# ------------------------------------------------------------ Recruiting

def customer_not_responded(today, now):
    from apps.recruiting.models import CandidateDisclosure
    rows = (CandidateDisclosure.objects.filter(revoked_at__isnull=True, feedback__isnull=True,
                                               released_at__gte=now - timedelta(days=60))
            .select_related("participant", "organization", "candidate__opportunity__owner"))
    for row in rows[:500]:
        person = row.participant
        yield Item(ref=f"disclosure:{row.pk}", anchor=_local(row.released_at).date(), created=row.released_at,
                   line=f"{row.organization} hasn't responded about {person.first_name} {person.last_name}",
                   link=reverse("recruiting:candidate-detail", args=[row.candidate_id]),
                   fields={"name": f"{person.first_name} {person.last_name}", "company": str(row.organization)},
                   related={"recruiter": _people(user_target(row.candidate.opportunity.owner))})


def employer_feedback(today, now):
    from apps.crm.models import Interaction
    from apps.recruiting.models import EmployerCandidateFeedback
    labels = {"interview": "asked for an interview with", "feedback": "sent feedback about",
              "interested": "is interested in", "report_hired": "reported hiring"}
    rows = (EmployerCandidateFeedback.objects.filter(kind__in=list(labels), confirmed_at__isnull=True,
                                                     created_at__gte=now - timedelta(days=30))
            .select_related("disclosure__participant", "disclosure__organization", "disclosure__candidate__opportunity__owner"))
    for row in rows[:300]:
        person = row.disclosure.participant
        # Handled once someone logs a note or call for this person after the request.
        if Interaction.objects.filter(participant=person, created_at__gt=row.created_at).exists():
            continue
        yield Item(ref=f"feedback:{row.pk}", anchor=_local(row.created_at).date(), created=row.created_at,
                   line=f"{row.disclosure.organization} {labels[row.kind]} {person.first_name} {person.last_name}",
                   link=reverse("recruiting:candidate-detail", args=[row.disclosure.candidate_id]),
                   fields={"name": f"{person.first_name} {person.last_name}", "company": str(row.disclosure.organization)},
                   related={"recruiter": _people(user_target(row.disclosure.candidate.opportunity.owner))})


def new_position(today, now):
    from apps.customers.models import WorkforceNeedRevision
    rows = (WorkforceNeedRevision.objects.filter(state="submitted", submitted_at__gte=now - timedelta(days=60))
            .select_related("organization", "submitted_by"))
    for row in rows[:300]:
        title = row.title or "a position"
        yield Item(ref=f"need-revision:{row.pk}", anchor=_local(row.submitted_at).date(), created=row.submitted_at,
                   line=f"{row.organization} submitted {title}",
                   link=reverse("customers:need", args=[row.organization_id, row.need_id]),
                   fields={"company": str(row.organization), "position": title}, related={})


# --------------------------------------------------------------- Payroll

def _period_start(today):
    return today - timedelta(days=today.weekday())


def missing_workdays(today, now):
    from apps.staffing.models import AssignmentDay
    from apps.workdays.models import Workday
    start = _period_start(today)
    cutoff = start + timedelta(days=6)
    days = (AssignmentDay.objects.filter(assignment__status="confirmed", date__gte=start, date__lte=min(today, cutoff))
            .select_related("assignment__staff", "assignment__staff__user", "assignment__event"))
    entered = set(Workday.objects.filter(work_date__gte=start, work_date__lte=cutoff, is_active=True)
                  .values_list("event_id", "staff_id", "work_date"))
    missing = {}
    for day in days:
        a = day.assignment
        if (a.event_id, a.staff_id, day.date) not in entered:
            missing.setdefault(a.staff, []).append(day.date)
    for staff, dates in missing.items():
        listed = ", ".join(f"{d:%a} {d.month}/{d.day}" for d in sorted(set(dates)))
        yield Item(ref=f"missing:{staff.pk}:{start.isoformat()}", anchor=cutoff,
                   line=f"{staff.first_name} {staff.last_name}: no workday entered for {listed}",
                   link=reverse("workdays:index"),
                   fields={"first_name": staff.first_name, "name": f"{staff.first_name} {staff.last_name}", "dates": listed},
                   related={"staff": _people(staff_target(staff))})


def payroll_ready(today, now):
    from apps.reporting.models import PayrollPeriod
    for row in PayrollPeriod.objects.filter(state="compiled", starts_on__gte=today - timedelta(days=90)):
        label = f"{row.starts_on:%b} {row.starts_on.day} – {row.ends_on:%b} {row.ends_on.day}"
        yield Item(ref=f"payroll:{row.starts_on.isoformat()}:{row.revision}", anchor=_local(row.updated_at).date(),
                   created=row.updated_at, line=f"Payroll for {label} is ready to approve",
                   link=reverse("reporting:payroll", args=[row.starts_on.isoformat()]), fields={"period": label}, related={})


def mileage_pending(today, now):
    """Mileage trips entered but not yet checked, for the pay week that's closing."""
    from apps.workdays.models import MileageTrip
    start = _period_start(today)
    cutoff = start + timedelta(days=6)
    rows = (MileageTrip.objects.filter(status="pending", claim_date__lte=cutoff, claim_date__gte=start - timedelta(days=28))
            .select_related("staff", "event"))
    waiting = {}
    for trip in rows[:500]:
        waiting.setdefault(trip.staff, []).append(trip)
    for staff, trips in waiting.items():
        yield Item(ref=f"mileage:{staff.pk}:{start.isoformat()}", anchor=cutoff,
                   line=f"{staff.first_name} {staff.last_name}: {len(trips)} mileage trip{'s' if len(trips) != 1 else ''} waiting to be checked",
                   link=reverse("workdays:index"),
                   fields={"name": f"{staff.first_name} {staff.last_name}", "count": str(len(trips))},
                   related={"staff": _people(staff_target(staff))})


def payroll_not_approved(today, now):
    """Pay weeks that have ended but aren't approved yet (the cutoff is the Sunday the week ends)."""
    from apps.reporting.models import PayrollPeriod
    from apps.workdays.models import Workday
    last_monday = _period_start(today) - timedelta(days=7)
    approved = set(PayrollPeriod.objects.filter(state="approved", starts_on__gte=last_monday - timedelta(days=21))
                   .values_list("starts_on", flat=True))
    for back in range(0, 4):
        starts = last_monday - timedelta(days=7 * back)
        ends = starts + timedelta(days=6)
        if starts in approved or not Workday.objects.filter(work_date__gte=starts, work_date__lte=ends, is_active=True).exists():
            continue  # approved already, or nobody worked that week
        label = f"{starts:%b} {starts.day} – {ends:%b} {ends.day}"
        yield Item(ref=f"payroll-open:{starts.isoformat()}", anchor=ends, line=f"Payroll for {label} isn't approved yet",
                   link=reverse("reporting:payroll", args=[starts.isoformat()]), fields={"period": label}, related={})


def report_ready(today, now):
    """An event's cost report is fully reviewed and ready to read."""
    from apps.event_reports.models import CostReview
    rows = (CostReview.objects.filter(manual_reviewed=True, incentives_reviewed=True, staff_reviewed=True,
                                      updated_at__gte=now - timedelta(days=30)).select_related("event"))
    for row in rows[:200]:
        event = row.event
        yield Item(ref=f"report:{event.pk}:{row.source_fingerprint[:12]}", anchor=_local(row.updated_at).date(), created=row.updated_at,
                   line=f"The cost report for {event.name} is ready", link=reverse("events:event_detail", args=[event.pk]),
                   fields={"event": event.name}, related={})


# ----------------------------------------------------------------- Admin

def connection_failing(today, now):
    from apps.messaging.models import ConnectionSetting, GmailConnection
    for row in ConnectionSetting.objects.filter(key__in=("eztexting", "email"), last_test_ok=False):
        yield Item(ref=f"connection:{row.key}:{row.last_test_at.isoformat() if row.last_test_at else ''}",
                   anchor=_local(row.last_test_at or row.updated_at).date(), created=row.last_test_at or row.updated_at,
                   line=f"{row.get_key_display()} isn't working: {row.last_test_detail or 'the last test failed'}",
                   link=reverse("messaging:connections"), fields={"connection": row.get_key_display()}, related={})
    for row in GmailConnection.objects.filter(needs_reconnect=True).select_related("user"):
        yield Item(ref=f"gmail:{row.pk}", anchor=today, created=now,
                   line=f"{row.user.get_full_name() or row.user}'s Gmail needs reconnecting",
                   link=reverse("internal:settings"), fields={"connection": "Gmail"},
                   related={"owner": _people(user_target(row.user))})


def messages_failing(today, now):
    from apps.events.models import OperationalNotificationDelivery, TicketDelivery
    from apps.messaging.models import EmailBounce, TextMessage
    since = now - timedelta(days=1)
    counts = {
        "tickets": TicketDelivery.objects.filter(status="failed", last_attempt_at__gte=since).count(),
        "alerts": OperationalNotificationDelivery.objects.filter(status="failed", created_at__gte=since).count(),
        "texts": TextMessage.objects.filter(direction="out", state="failed", created_at__gte=since).count(),
        "bounces": EmailBounce.objects.filter(bounced_at__gte=since, cleared_at__isnull=True).count(),
    }
    total = sum(counts.values())
    if total:
        parts = [f"{n} {label}" for label, n in (("ticket messages", counts["tickets"]), ("alerts", counts["alerts"]),
                                                  ("texts", counts["texts"]), ("bounced emails", counts["bounces"])) if n]
        yield Item(ref=f"failures:{today.isoformat()}", anchor=today, created=now,
                   line="Didn't go out in the last day: " + ", ".join(parts), link=reverse("messaging:connections"),
                   fields={"count": str(total), "details": ", ".join(parts)}, related={})


# ------------------------------------------------------------- Customers

def onboarding_submitted(today, now):
    from apps.onboarding.models import CustomerOnboarding
    for row in CustomerOnboarding.objects.filter(status="submitted").select_related("created_by")[:200]:
        yield Item(ref=f"onboarding:{row.pk}", anchor=_local(row.submitted_at or row.updated_at).date(),
                   created=row.submitted_at or row.updated_at, line=f"{row.name} finished their onboarding form",
                   link=reverse("onboarding:step", args=[row.pk, "review"]), fields={"company": row.name},
                   related={"owner": _people(user_target(row.created_by))})


def delivery_behind(today, now):
    from apps.billing.delivery import progress
    from apps.billing.models import PositionCharge
    seen = set()
    rows = (PositionCharge.objects.filter(status__in=["paid", "processing"], kind__in=["skilled", "general"],
                                          need__status="active", created_at__lte=now - timedelta(days=14))
            .select_related("need__effective_revision", "need__recruiting_opportunity__owner",
                            "organization__billing__account_manager").order_by("-created_at"))
    for charge in rows[:300]:
        need = charge.need
        if need.pk in seen:
            continue
        seen.add(need.pk)
        p = progress(need)
        if not p["behind"]:
            continue
        org = charge.organization
        profile = getattr(org, "billing", None)
        opportunity = getattr(need, "recruiting_opportunity", None)
        unit = "hired" if p["unit"] == "people" else "delivered"
        done = p["hired"] if p["unit"] == "people" else p["delivered"]
        yield Item(ref=f"delivery:{need.pk}", anchor=_local(charge.created_at).date(), created=charge.created_at,
                   line=f"{org.name} · {p['title']}: {done} of {p['paid_for']} {unit}",
                   link=reverse("customers:workspace", args=[org.pk, "billing"]),
                   fields={"company": org.name, "position": p["title"], "remaining": str(p["remaining"])},
                   related={"recruiter": _people(user_target(opportunity.owner if opportunity else None)),
                            "account_manager": _people(user_target(profile.account_manager if profile else None))})


def documents_expiring(today, now):
    from apps.onboarding.models import CustomerDocument
    rows = (CustomerDocument.objects.filter(removed_at__isnull=True, expires_on__isnull=False,
                                            expires_on__gte=today - timedelta(days=1), expires_on__lte=today + timedelta(days=90))
            .select_related("organization__billing__account_manager"))
    for row in rows:
        org = row.organization
        profile = getattr(org, "billing", None)
        ends = f"{row.expires_on:%B} {row.expires_on.day}, {row.expires_on.year}"
        yield Item(ref=f"document:{row.pk}:{row.expires_on.isoformat()}", anchor=row.expires_on,
                   line=f"{org.name}: {row.title} expires {ends}",
                   link=reverse("customers:workspace", args=[org.pk, "documents"]),
                   fields={"company": org.name, "document": row.title, "end_date": ends},
                   related={"account_manager": _people(user_target(profile.account_manager if profile else None))})


# --------------------------------------------------------------- Billing

def agreement_ending(today, now):
    from apps.billing.models import MonthlyAgreement
    rows = (MonthlyAgreement.objects.filter(status__in=["active", "scheduled", "waiting"], ends_on__gte=today,
                                            ends_on__lte=today + timedelta(days=120), renewals__isnull=True)
            .select_related("organization__primary_contact", "organization__billing__account_manager"))
    for row in rows:
        org = row.organization
        profile = getattr(org, "billing", None)
        manager = profile.account_manager if profile else None
        email = profile.contact_email() if profile else (org.primary_contact.email if org.primary_contact_id else "")
        contact_name = str(org.primary_contact) if org.primary_contact_id else org.name
        contact = Target(f"c:{org.pk}", contact_name, email=email) if email else None
        ends = f"{row.ends_on:%B} {row.ends_on.day}, {row.ends_on.year}"
        yield Item(ref=f"agreement:{row.pk}", anchor=row.ends_on,
                   line=f"{org.name}'s monthly agreement (${row.amount:,.2f}) ends {ends}",
                   link=reverse("customers:workspace", args=[org.pk, "billing"]),
                   fields={"company": org.name, "end_date": ends, "amount": f"${row.amount:,.2f}",
                           "manager": (manager.get_full_name() or manager.get_username()) if manager else "your Built to Work account manager"},
                   related={"customer_contact": _people(contact), "account_manager": _people(user_target(manager))})


def billing_problems(today, now):
    from apps.billing.models import MonthlyAgreement, PositionCharge
    charges = (PositionCharge.objects.filter(status__in=["failed", "waiting", "not_set_up"], created_at__gte=now - timedelta(days=90))
               .select_related("organization__billing__account_manager"))
    for row in charges[:300]:
        org = row.organization
        profile = getattr(org, "billing", None)
        why = {"failed": "the bank payment failed", "waiting": "they haven't connected a bank account",
               "not_set_up": "Stripe isn't connected"}[row.status]
        yield Item(ref=f"charge:{row.pk}:{row.status}", anchor=_local(row.updated_at).date(), created=row.updated_at,
                   line=f"{org.name}: ${row.amount:,.2f} for {row.title} not collected — {why}",
                   link=reverse("customers:workspace", args=[org.pk, "billing"]), fields={"company": org.name, "why": why},
                   related={"account_manager": _people(user_target(profile.account_manager if profile else None))})
    for row in (MonthlyAgreement.objects.filter(last_payment_status="failed", last_payment_at__gte=now - timedelta(days=60))
                .select_related("organization__billing__account_manager")):
        org = row.organization
        profile = getattr(org, "billing", None)
        yield Item(ref=f"agreement-payment:{row.pk}:{row.last_payment_at.isoformat()}", anchor=_local(row.last_payment_at).date(),
                   created=row.last_payment_at, line=f"{org.name}: monthly payment of ${row.amount:,.2f} failed",
                   link=reverse("customers:workspace", args=[org.pk, "billing"]), fields={"company": org.name, "why": "the monthly payment failed"},
                   related={"account_manager": _people(user_target(profile.account_manager if profile else None))})


# --------------------------------------------------------- New people

def _age(dob, today):
    if not dob:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def new_participants(today, now):
    from apps.participants.intake_discovery import current_interest_expression
    from apps.participants.models import Participant
    rows = (Participant.objects.filter(kind="person", archived_at__isnull=True, created_at__gte=now - timedelta(days=4))
            .exclude(registration_submissions__source="historical_import")
            .annotate(_interest=current_interest_expression())
            .prefetch_related("registration_submissions__event", "registration_submissions__intake_session")
            .order_by("created_at"))
    for person in rows[:500]:
        subs = sorted(person.registration_submissions.all(), key=lambda x: x.pk)
        sub = subs[0] if subs else None
        session = getattr(sub, "intake_session", None) if sub else None
        if sub and sub.event_id:
            joined, where = "event", f"at {sub.event.name}"
        elif sub and (getattr(session, "channel", "") == "online" or sub.source in ("public", "online", "website")):
            joined, where = "website", "on the website"
        else:
            joined, where = "staff", "added by staff"
        name = f"{person.preferred_name or person.first_name} {person.last_name}".strip()
        kind = person.get_primary_identifier_display() if person.primary_identifier else ""
        place = ", ".join(p for p in (person.city, person.state) if p)
        trades = (person._interest or "").strip()
        bits = [b for b in (kind, place, trades and f"interested in {trades}") if b]
        yield Item(ref=f"participant:{person.pk}", created=person.created_at, anchor=_local(person.created_at).date(),
                   line=f"{name} signed up {where}" + (f" ({'; '.join(bits)})" if bits else ""),
                   link=reverse("participants:staff-profile", args=[person.pk]),
                   fields={"name": name, "type": kind, "place": place, "trades": trades, "joined": where},
                   facts={"type": person.primary_identifier, "state": person.state or "", "trades": trades,
                          "age": _age(person.date_of_birth, today), "joined": joined,
                          "event": str(sub.event_id) if sub and sub.event_id else ""})


def candidates_shared(today, now):
    from apps.customers.models import CustomerMembership
    from apps.recruiting.models import CandidateDisclosure
    rows = (CandidateDisclosure.objects.filter(revoked_at__isnull=True, released_at__gte=now - timedelta(days=4))
            .select_related("participant", "organization", "need_revision", "candidate__opportunity__owner"))
    teams = {}
    for row in rows[:500]:
        org = row.organization
        if org.pk not in teams:
            members = (CustomerMembership.objects.filter(organization=org, state="enabled", identity__activated_at__isnull=False)
                       .select_related("identity"))
            teams[org.pk] = members
        portal = reverse("customer_portal:recruiting-candidate", args=[row.pk])
        team = [Target(f"ci:{m.identity_id}", f"{m.identity.first_name} {m.identity.last_name}".strip(),
                       email=m.identity.email, phone=m.identity.phone or "", portal_url=portal) for m in teams[org.pk]]
        person = row.participant
        shown = f"{person.preferred_name or person.first_name} {person.last_name[:1]}.".strip()
        position = getattr(row.need_revision, "title", "") or "your open position"
        yield Item(ref=f"shared:{row.pk}", created=row.released_at, anchor=_local(row.released_at).date(),
                   line=f"New candidate for {org.name} ({position}): {shown}",
                   link=reverse("recruiting:candidate-detail", args=[row.candidate_id]),
                   fields={"name": shown, "company": org.name, "position": position},
                   related={"customer_team": team,
                            "recruiter": _people(user_target(row.candidate.opportunity.owner))})


# Conditions a reminder can be limited by. Each: key, label, kind.
PARTICIPANT_FILTERS = (
    ("type", "Participant type", "choices", (("workforce", "Workforce"), ("student", "Student"),
                                             ("teacher_instructor", "Teacher / Instructor"), ("other", "Other"))),
    ("joined", "How they signed up", "choices", (("website", "Website form"), ("event", "At an event"), ("staff", "Added by staff"))),
    ("states", "States", "words", "Like GA, SC. Leave blank for anywhere."),
    ("trades", "Trade interests", "words", "Like Welding, Heavy Equipment. Matches any of them. Leave blank for all."),
    ("adults", "18 and older only", "flag", ""),
)

STATE_NAMES = {"al": "alabama", "fl": "florida", "ga": "georgia", "nc": "north carolina", "sc": "south carolina",
               "tn": "tennessee", "va": "virginia", "ms": "mississippi", "ky": "kentucky", "tx": "texas"}


def _words(value):
    return [w.strip().casefold() for w in str(value or "").split(",") if w.strip()]


def matches(conditions, item):
    """True when the item meets every condition the reminder has."""
    if not conditions:
        return True
    facts = item.facts or {}
    if conditions.get("type") and facts.get("type") not in conditions["type"]:
        return False
    if conditions.get("joined") and facts.get("joined") not in conditions["joined"]:
        return False
    states = _words(conditions.get("states"))
    if states:
        have = (facts.get("state") or "").strip().casefold()
        wanted = set(states) | {STATE_NAMES.get(s, s) for s in states} | {k for k, v in STATE_NAMES.items() if v in states}
        if not have or have not in wanted:
            return False
    trades = _words(conditions.get("trades"))
    if trades and not any(t in (facts.get("trades") or "").casefold() for t in trades):
        return False
    if conditions.get("adults") and (facts.get("age") is None or facts["age"] < 18):
        return False
    return True


@dataclass(frozen=True)
class Source:
    key: str
    area: str
    label: str
    finder: object
    modes: tuple
    anchor_label: str
    related: dict
    fields: dict
    filters: tuple = ()


COMMON = {"line": "a one-line summary of the item"}

SOURCES = {s.key: s for s in (
    Source("staff_working_soon", "Events", "Staff working an event", staff_working_soon, ("before",),
           "their first day on the event", {"staff": "Each staff member on the event"},
           {"first_name": "their first name", "event": "event name", "when": "arrival day and time", "place": "location"}),
    Source("event_changed", "Events", "Event date, time or place", event_changed, ("changed",),
           "the event", {"staff": "Each staff member on the event"},
           {"event": "event name", "when": "new start day and time", "place": "location"}),
    Source("participant_event", "Participants", "Participants signed up for an event (texts allowed)",
           participant_event_day_before, ("before",), "the event day", {"participant": "The participant"},
           {"first_name": "their first name", "event": "event name", "when": "start day and time", "place": "location"}),
    Source("new_participant", "Participants", "New participants", new_participants, ("new",), "they sign up", {},
           {"name": "the participant", "type": "participant type", "place": "city and state", "trades": "trade interests",
            "joined": "where they signed up"}, PARTICIPANT_FILTERS),
    Source("candidate_shared", "Recruiting", "Candidates shared with a customer", candidates_shared, ("new",),
           "the candidate is shared", {"customer_team": "The customer's portal users", "recruiter": "Recruiter on the position"},
           {"name": "candidate (first name and last initial)", "company": "the customer", "position": "the position"}),
    Source("follow_up", "Participants", "Follow-up dates on touchpoints", follow_ups, ("before", "after"),
           "the follow-up date", {"owner": "Person who set the follow-up"}, {"name": "who to follow up with", "summary": "the note"}),
    Source("text_reply", "Participants", "Text replies still marked New", unhandled_text_replies, ("after", "new"),
           "the reply", {"owner": "Person the reply went to"}, {"name": "who texted"}),
    Source("missing_info", "Participants", "Requests for missing info", missing_info, ("after", "new"),
           "asking", {"owner": "Staff member who asked"}, {"name": "the participant"}),
    Source("new_interest", "Participants", "New interest in a position", new_interest, ("new", "after"),
           "they show interest", {"recruiter": "Recruiter for the position"}, {"name": "the participant"}),
    Source("customer_no_response", "Recruiting", "Candidates sent to a customer, no response yet", customer_not_responded,
           ("after",), "the candidate is sent", {"recruiter": "Recruiter on the position"},
           {"name": "the candidate", "company": "the customer"}),
    Source("employer_feedback", "Recruiting", "Interview requests and customer feedback", employer_feedback, ("new", "after"),
           "the customer asks", {"recruiter": "Recruiter on the position"}, {"name": "the candidate", "company": "the customer"}),
    Source("new_position", "Recruiting", "Positions submitted by customers", new_position, ("new", "after"),
           "it's submitted", {}, {"company": "the customer", "position": "position title"}),
    Source("onboarding_submitted", "Customers", "Customer onboarding forms ready to review", onboarding_submitted,
           ("new", "after"), "when they submitted it", {"owner": "Staff member who started it"}, {"company": "the customer"}),
    Source("delivery_behind", "Recruiting", "Paid-for positions still short on candidates", delivery_behind, ("after",),
           "when the position was charged", {"recruiter": "Recruiter on the position", "account_manager": "BTW account manager"},
           {"company": "the customer", "position": "position title", "remaining": "how many are still owed"}),
    Source("documents_expiring", "Customers", "Customer documents expiring", documents_expiring, ("before",),
           "the document's expiration date", {"account_manager": "BTW account manager"},
           {"company": "the customer", "document": "document name", "end_date": "expiration date"}),
    Source("agreement_ending", "Billing", "Monthly agreements ending", agreement_ending, ("before",),
           "the agreement's end date", {"customer_contact": "The customer's main (billing) contact",
                                        "account_manager": "BTW account manager"},
           {"company": "the customer", "end_date": "the end date", "amount": "monthly amount", "manager": "account manager's name"}),
    Source("billing_problem", "Billing", "Payments not collected", billing_problems, ("new", "after"),
           "when it happened", {"account_manager": "BTW account manager"}, {"company": "the customer", "why": "what went wrong"}),
    Source("missing_workdays", "Payroll", "Workdays not entered this pay week", missing_workdays, ("before",),
           "the end of the pay week (Sunday)", {"staff": "Staff with missing entries"},
           {"first_name": "their first name", "name": "staff member", "dates": "the missing days"}),
    Source("payroll_ready", "Payroll", "Payroll ready to approve", payroll_ready, ("new", "after"),
           "it's ready", {}, {"period": "the pay week"}),
    Source("mileage_pending", "Payroll", "Mileage waiting to be checked", mileage_pending, ("before",),
           "the end of the pay week (Sunday)", {"staff": "Staff with trips waiting"},
           {"name": "staff member", "count": "how many trips"}),
    Source("payroll_not_approved", "Payroll", "Pay weeks not approved yet", payroll_not_approved, ("after",),
           "the end of the pay week (Sunday)", {}, {"period": "the pay week"}),
    Source("report_ready", "Events", "Event cost reports ready to read", report_ready, ("new",),
           "it's reviewed", {}, {"event": "event name"}),
    Source("connection_failing", "Admin", "Texting, email or Gmail sign-in failing", connection_failing, ("new", "after"),
           "it fails", {"owner": "The person whose Gmail it is"}, {"connection": "which connection"}),
    Source("messages_failing", "Admin", "Messages that didn't go out", messages_failing, ("after", "new"),
           "each day", {}, {"count": "how many", "details": "what failed"}),
)}


def choices():
    return [(s.key, f"{s.area} · {s.label}") for s in SOURCES.values()]


def admins():
    return [t for t in (user_target(u) for u in get_user_model().objects.filter(is_active=True, is_superuser=True)
                        .select_related("staff_profile").order_by("first_name")) if t]
