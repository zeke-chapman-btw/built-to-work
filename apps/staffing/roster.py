"""Staff roster: pay summary per person, archive/restore and delete (only with no history)."""
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import manager
from apps.core.audit import record_audit
from .models import StaffProfile

ZERO = Decimal("0")
KEYS = ("base", "per_diem", "mileage", "incentives", "adjustments", "total")


def can_see_pay(user):
    """Same people who can open the weekly payroll report."""
    return manager(user)


def statements_for(profile):
    """Approved weekly earnings statements for one person, newest first (latest version per week)."""
    from django.db.models import OuterRef, Subquery
    from apps.reporting.models import ContractorStatement, PayrollApproval
    from apps.reporting.payroll import _private_statement
    newest = PayrollApproval.objects.filter(version__period_id=OuterRef("approval__version__period_id")).order_by("-version__number").values("pk")[:1]
    rows = (ContractorStatement.objects.filter(staff=profile, approval_id=Subquery(newest))
            .select_related("approval__version__period").defer("approval__version__snapshot").order_by("-approval__version__period_id"))
    for row in rows:
        yield row, _private_statement(row)


def _day(value):
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def pay_summary(profile):
    from apps.reporting.definitions import REPORT_TIMEZONE
    today = timezone.localdate(timezone.now(), ZoneInfo(REPORT_TIMEZONE))
    year = {k: ZERO for k in KEYS}
    ever = {k: ZERO for k in KEYS}
    days_year, days_ever, stubs = set(), set(), []
    for row, data in statements_for(profile):
        week_total = ZERO
        for line in data.get("rows", []):
            when = str(line.get("occurred_on") or "")[:10]
            for key in KEYS:
                if line.get(key) is not None:
                    ever[key] += Decimal(line[key])
                    if when[:4] == str(today.year):
                        year[key] += Decimal(line[key])
            if line.get("total") is not None:
                week_total += Decimal(line["total"])
            if when and line.get("source_kind", "workday") == "workday":
                days_ever.add((line.get("event"), when))
                if when[:4] == str(today.year):
                    days_year.add((line.get("event"), when))
        stubs.append(dict(start=_day(data.get("period_start")), end=_day(data.get("period_end")),
                          total=Decimal(str(data.get("totals", {}).get("total") or week_total)), status=data.get("status", "Approved")))
    for totals in (year, ever):
        totals["reimbursements"] = totals["per_diem"] + totals["mileage"]
        totals["compensation"] = totals["base"] + totals["incentives"] + totals["adjustments"]
    return dict(year=today.year, ytd=year, all_time=ever, days_ytd=len(days_year), days_all=len(days_ever), stubs=stubs)


def _history_counts(profile):
    counts = {}
    for rel in StaffProfile._meta.related_objects:
        accessor = rel.get_accessor_name()
        if rel.one_to_one:
            continue
        manager_ = getattr(profile, accessor, None)
        if manager_ is not None and hasattr(manager_, "exists") and manager_.exists():
            counts[rel.related_model._meta.verbose_name_plural] = manager_.count()
    return counts


def can_delete(profile):
    return not profile.user_id and not _history_counts(profile)


@transaction.atomic
def set_archived(*, actor, profile_id, archived):
    if not manager(actor):
        raise PermissionDenied
    profile = StaffProfile.objects.select_for_update().get(pk=profile_id)
    if profile.user_id:
        from apps.accounts.roles import require_target
        require_target(actor, profile.user)
    profile.is_active = not archived
    profile.save(update_fields=["is_active", "updated_at"])
    if profile.user_id and profile.user.is_active == archived:
        from apps.accounts.staff_services import set_active, revision
        set_active(actor=actor, user_id=profile.user_id, active=not archived, expected_revision=revision(profile.user),
                   reason="Archived from the staff roster" if archived else "Restored from the staff roster")
    record_audit(actor=actor, action="staff.archived" if archived else "staff.restored", instance=profile, source="staff_roster")
    return profile


@transaction.atomic
def delete_profile(*, actor, profile_id):
    if not manager(actor):
        raise PermissionDenied
    profile = StaffProfile.objects.select_for_update().get(pk=profile_id)
    if not can_delete(profile):
        raise ValidationError("This person has a login or work history, so they can't be deleted. Archive them instead.")
    record_audit(actor=actor, action="staff.deleted", instance=profile, source="staff_roster",
                 old_data={"name": str(profile), "email": profile.email})
    profile.delete()
