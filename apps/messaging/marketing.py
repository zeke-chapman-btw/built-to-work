"""Marketing email consent: record it, change it, and keep a history."""
from django.db import transaction
from django.utils import timezone

from apps.core.audit import record_audit

from .models import MARKETING_WORDING, MarketingConsent

ON = MarketingConsent.Status.ON


def current(participant):
    return MarketingConsent.objects.filter(participant=participant).first()


def status_of(participant):
    row = current(participant)
    return row.status if row else MarketingConsent.Status.OFF


@transaction.atomic
def set_status(*, participant, status, source, actor=None, note="", wording="", submission=None, when=None):
    """Change a person's marketing email status; returns the row. History goes to the audit log."""
    row = MarketingConsent.objects.select_for_update().filter(participant=participant).first()
    before = {"status": row.status, "source": row.source} if row else None
    if row and row.status == status and not wording:
        return row
    if row is None:
        row = MarketingConsent(participant=participant)
    row.status, row.source = status, source
    row.note = (note or "")[:300]
    row.wording = wording if status == ON else row.wording
    row.changed_at = when or timezone.now()
    row.changed_by = actor
    row.submission = submission if submission is not None else row.submission
    row.needs_sync = True
    row.save()
    record_audit(actor=actor, action="messaging.marketing_consent.changed", instance=row, source="marketing_consent",
                 old_data=before, new_data={"status": row.status, "source": row.source, "note": row.note,
                                            "participant_id": participant.pk},
                 reason=note or f"Marketing email set to {row.get_status_display()} ({row.get_source_display()}).")
    return row


def record_signup(*, participant, submission, agreed_at):
    """A ticked sign-up checkbox turns marketing email on. An unticked box changes nothing."""
    return set_status(participant=participant, status=ON, source=MarketingConsent.Source.SIGNUP,
                      wording=MARKETING_WORDING, submission=submission, when=agreed_at,
                      note="Ticked the marketing email box on the sign-up form.")


def history(participant, limit=20):
    from apps.core.models import AuditLog
    row = current(participant)
    if not row:
        return []
    return list(AuditLog.objects.filter(source="marketing_consent", object_id=str(row.pk))
                .select_related("actor").order_by("-timestamp")[:limit])
