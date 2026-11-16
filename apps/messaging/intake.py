"""Carry the optional marketing checkbox from the sign-up consent page to the saved record."""
from django.db import transaction
from django.utils import timezone

from .models import MARKETING_WORDING


@transaction.atomic
def remember_choice(session, opted_in):
    from apps.participants.intake_models import IntakeSession
    row = IntakeSession.objects.select_for_update().get(pk=session.pk)
    row.context = dict(row.context or {}, marketing_email={
        "opt_in": bool(opted_in), "at": timezone.now().isoformat() if opted_in else "", "wording": MARKETING_WORDING})
    row.save(update_fields=["context"])
    session.context = row.context


def apply_choice(session_id):
    from django.utils.dateparse import parse_datetime
    from apps.participants.intake_models import IntakeSession
    from . import marketing
    session = IntakeSession.objects.select_related("submission__participant").filter(pk=session_id).first()
    choice = (session.context or {}).get("marketing_email") if session else None
    submission = getattr(session, "submission", None) if session else None
    if not (choice and choice.get("opt_in") and submission and submission.participant_id):
        return None
    return marketing.record_signup(participant=submission.participant, submission=submission,
                                   agreed_at=parse_datetime(choice.get("at") or "") or timezone.now())
