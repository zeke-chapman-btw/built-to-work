from decimal import Decimal,InvalidOperation
from django import template
register=template.Library()
@register.filter
def amount(value):
    if value is None:return 'Unavailable'
    try:return format(Decimal(str(value)),',.2f')
    except (ValueError,InvalidOperation):return 'Unavailable'
@register.filter
def money(value):
    """Dollar amount, or a grey dash when it can't be worked out yet."""
    from django.utils.html import format_html
    try:
        return format_html('${}', format(Decimal(str(value)), ',.2f')) if value is not None else format_html('<span class="pv-unset" title="Not final yet">—</span>')
    except (ValueError, InvalidOperation):
        return format_html('<span class="pv-unset" title="Not final yet">—</span>')
