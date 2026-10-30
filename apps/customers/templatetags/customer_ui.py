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
