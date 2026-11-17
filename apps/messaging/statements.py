"""Tell staff by email that a new earnings statement is ready to view in My pay.

The email carries a sign-in link only: no amounts, no attachment. Off until an
administrator turns on "Pay statement emails" (and Automatic messages) in Connections.
"""
import logging

from django.urls import reverse

from . import signatures, store
from .models import ConnectionSetting, StatementNotice

log = logging.getLogger(__name__)


def enabled():
    row = ConnectionSetting.objects.filter(key="statement_notices").first()
    return bool(row and row.values.get("enabled")) and store.automatic_sending_enabled()


def set_enabled(*, actor, on):
    store.save(key="statement_notices", actor=actor, values={"enabled": bool(on)})


def _link():
    from apps.participants.portal_accounts import portal_base_url
    base = portal_base_url()
    return f"{base}{reverse('reporting:my-pay')}" if base else ""


def notify(approval_id):
    from apps.reporting.models import ContractorStatement
    if not enabled():
        return 0
    config = store.email_settings()
    connection = store.smtp_connection()
    if connection is None or not config.get("from_address"):
        return 0
    sent = 0
    rows = (ContractorStatement.objects.filter(approval_id=approval_id, notice__isnull=True)
            .select_related("staff__user", "approval__version__period"))
    for row in rows:
        profile = row.staff
        user = profile.user
        address = ((user.email if user else "") or profile.email or "").strip()
        if not (address and profile.my_pay_enabled and user and user.is_active):
            StatementNotice.objects.create(statement=row, address=address, status=StatementNotice.Status.SKIPPED)
            continue
        period = row.approval.version.period
        week = getattr(period, "starts_on", None)
        when = f" for the week of {week:%B} {week.day}" if week else ""
        link = _link()
        body = (f"Hi {profile.first_name},\n\nYour Built to Work earnings statement{when} is ready."
                f"{(' Sign in to view it in My pay: ' + link) if link else ' Sign in to the app and open My pay to view it.'}\n\n"
                "For your privacy this email doesn't include any amounts.")
        message = signatures.build("Your earnings statement is ready", body, config["from_address"], [address],
                                   signature_html=signatures.company(), reply_to=config.get("reply_to") or None,
                                   connection=connection)
        try:
            ok = message.send(fail_silently=False) == 1
            StatementNotice.objects.create(statement=row, address=address,
                                           status=StatementNotice.Status.SENT if ok else StatementNotice.Status.FAILED)
            sent += int(ok)
        except Exception as exc:  # mail server errors vary; the statement itself is unaffected
            log.warning("STATEMENT_NOTICE_FAILED %s", type(exc).__name__)
            StatementNotice.objects.create(statement=row, address=address, status=StatementNotice.Status.FAILED,
                                           detail=type(exc).__name__)
    return sent
