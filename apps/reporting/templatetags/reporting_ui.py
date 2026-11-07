from decimal import Decimal,InvalidOperation
from django import template
register=template.Library()
@register.filter
def money(value):
    if value is None or value=="":return "Unavailable"
    try:return "$"+format(Decimal(str(value)),",.2f")
    except (InvalidOperation,ValueError,TypeError):return "Unavailable"
@register.filter
def figure(value):
    if value is None:return "Unavailable"
    if isinstance(value,Decimal):return format(value.quantize(Decimal(".01")),"f")
    return value
