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
            "csrf_token": context.get("csrf_token")}


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
