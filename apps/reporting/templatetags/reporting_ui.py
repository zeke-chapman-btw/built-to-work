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

@register.filter
def add_money(value, other):
    """Add two money amounts for display; blank parts count as zero."""
    total = Decimal("0")
    for part in (value, other):
        try:
            total += Decimal(str(part)) if part not in (None, "") else Decimal("0")
        except (InvalidOperation, ValueError, TypeError):
            return None
    return total
