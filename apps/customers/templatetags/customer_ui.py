from urllib.parse import urlencode
from django import template
from django.urls import reverse
from apps.customers.views import local_return

register = template.Library()


@register.simple_tag(takes_context=True)
def customer_url(context, name, *args):
    """Carry a validated directory return context through nested customer tools."""
    url = reverse(name, args=args)
    request = context["request"]
    back = local_return(request, context.get("back_url"), reverse("customers:directory"))
    return url + "?" + urlencode({"return_to": back})


@register.filter
def customer_initials(name):
    """Two letters for the customer avatar, skipping a leading SAMPLE marker."""
    words = [w for w in str(name or "").split() if w.upper() != "SAMPLE"] or str(name or "?").split()
    return "".join(word[0] for word in words[:2]).upper() or "?"


@register.filter
def customer_status_tone(status):
    """Shared badge tones: active is positive, prospect is informational, the rest neutral."""
    return {"active": "success", "prospect": "info"}.get(str(status), "neutral")


POSITION_STATUS = {"draft": ("Draft", "neutral"), "submitted": ("To review", "warning"), "review": ("To review", "warning"),
                   "active": ("Open", "success"), "hold": ("On hold", "warning"), "closed": ("Filled", "neutral"),
                   "cancelled": ("Cancelled", "neutral")}
CAMPAIGN_STATUS = {"planning": ("Planning", "info"), "active": ("Active", "success"), "hold": ("On hold", "warning"),
                   "completed": ("Completed", "neutral"), "cancelled": ("Cancelled", "neutral")}


@register.filter
def position_status(status):
    """Plain label and badge tone for a customer's open position."""
    return POSITION_STATUS.get(str(status), (str(status).title(), "neutral"))


@register.filter
def campaign_status(status):
    """Plain label and badge tone for a customer campaign."""
    return CAMPAIGN_STATUS.get(str(status), (str(status).title(), "neutral"))
