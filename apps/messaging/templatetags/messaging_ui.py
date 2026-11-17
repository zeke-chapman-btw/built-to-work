from django import template
from django.urls import reverse

from apps.messaging import google
from apps.messaging.models import GmailConnection

register = template.Library()


@register.inclusion_tag("messaging/gmail_card.html", takes_context=True)
def gmail_card(context):
    request = context["request"]
    return {"request": request, "configured": google.configured(), "next": reverse("my:schedule"),
            "connection": GmailConnection.objects.filter(user=request.user).first(),
            "replies_on": _replies_on(), "csrf_token": context.get("csrf_token")}


def _replies_on():
    from apps.messaging.email_replies import site_enabled
    return site_enabled()


@register.inclusion_tag("messaging/ticket_messages.html")
def ticket_messages(registration):
    from apps.messaging.models import TextDeliveryResult
    rows = list(registration.deliveries.order_by("created_at"))
    results = {r.provider_reference: r for r in TextDeliveryResult.objects.filter(
        provider_reference__in=[d.provider_reference for d in rows if d.provider_reference])}
    items = []
    for d in rows:
        label, tone = {"queued": ("Waiting to send", "b"), "failed": ("Failed", "r"),
                       "uncertain": ("Unconfirmed", "y")}.get(d.status, ("Sent", "g" if d.channel == "email" else "b"))
        note = d.last_error if d.status in ("failed", "uncertain") else ""
        result = results.get(d.provider_reference)
        if d.status == "sent" and d.channel == "sms" and result:
            if result.result == "delivered":
                label, tone = "Delivered", "g"
            elif result.result == "failed":
                label, tone, note = "Not delivered", "r", result.detail
        items.append({"channel": d.get_channel_display(), "label": label, "tone": tone, "note": note,
                      "when": d.sent_at or d.last_attempt_at or d.created_at, "attempts": d.attempts})
    return {"items": items}


@register.inclusion_tag("messaging/marketing_row.html", takes_context=True)
def marketing_email_row(context, participant, can_edit=False):
    from apps.messaging.marketing import current
    row = current(participant)
    tone = {"on": "g", "unsubscribed": "y", "bounced": "r"}.get(row.status if row else "off", "")
    request = context.get("request")
    return {"row": row, "tone": tone, "participant": participant, "can_edit": can_edit and not participant.archived_at,
            "return_to": request.get_full_path() if request else ""}


@register.inclusion_tag("messaging/text_card.html", takes_context=True)
def text_replies_card(context):
    from apps.messaging.text_views import new_reply_count
    from apps.participants.workspace_access import can_notes
    user = context["request"].user
    try:
        allowed = bool(can_notes(user))
    except Exception:
        allowed = False
    return {"show": allowed, "count": new_reply_count(user) if allowed else 0}


@register.inclusion_tag("messaging/account_email_card.html", takes_context=True)
def account_email_card(context):
    """Settings → Account: your Gmail connection and your email signature."""
    from apps.messaging import google, signatures
    from apps.messaging.models import GmailConnection
    request = context["request"]
    return {"configured": google.configured(), "connection": GmailConnection.objects.filter(user=request.user).first(),
            "signature": signatures.personal(request.user)}
