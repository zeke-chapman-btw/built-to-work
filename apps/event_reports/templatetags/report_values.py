from decimal import Decimal,InvalidOperation
from django import template
register=template.Library()
@register.filter
def amount(value):
    if value is None:return 'Unavailable'
    try:return format(Decimal(str(value)),',.2f')
    except (ValueError,InvalidOperation):return 'Unavailable'
