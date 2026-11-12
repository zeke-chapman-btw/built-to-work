from django import template

from apps.events.ticket_text import event_dates as _dates, event_place as _place

register = template.Library()


@register.filter
def event_dates(event):
    return _dates(event)


@register.filter
def event_place(event):
    return _place(event)
