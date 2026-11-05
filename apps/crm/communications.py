"""External draft convenience only. No sends, mailbox access or consent creation."""
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def gmail_compose(email):
    value = str(email or "").strip()
    try:
        validate_email(value)
    except ValidationError:
        return ""
    return "https://mail.google.com/mail/?" + urlencode({"view": "cm", "fs": "1", "to": value})


def participant_preferences(participant):
    from apps.participants.portal_accounts import intake_for
    from apps.participants.intake_contracts import canonical_answers
    submission = intake_for(participant)
    value = "unknown"
    if submission:
        value = canonical_answers(submission.form_version.snapshot, submission.answers).get("updates_preference", "unknown")
    labels = {"yes": "Optional updates: Yes (latest submitted intake)",
        "no": "Optional updates: No (latest submitted intake)", "unknown": "Optional updates preference not recorded"}
    return value, labels.get(value, labels["unknown"])


def relationship_communications(*, participant=None, organization=None):
    if participant:
        preference, label = participant_preferences(participant)
        return {"email_url": gmail_compose(participant.contact_email) if not participant.archived_at and preference != "no" else "",
            "preference": label, "email_help": "Draft disabled: the latest intake declines optional updates." if preference == "no" else
                "Confirm the purpose and applicable contact permissions before sending. A draft does not establish consent."}
    return {"email_url": gmail_compose(organization.business_email) if organization and organization.status != "archived" and
                not company_email_suppressed(organization, organization.business_email) else "",
        "preference": "Company correspondence · contact-specific preferences are shown below.",
        "email_help": "Use the appropriate business contact and check their preferences before sending."}


def contact_compose(contact):
    if not contact.is_active or contact.organization.status == "archived" or company_email_suppressed(contact.organization, contact.email):
        return ""
    return gmail_compose(contact.email)


def company_email_suppressed(organization, email):
    from django.db.models import Q
    # A duplicate contact row or the general company address must not bypass a recorded suppression.
    return bool(email) and organization.contacts.filter(email__iexact=email).filter(
        Q(is_active=False) | Q(crm_relationship__email_preference="avoid")).exists()
