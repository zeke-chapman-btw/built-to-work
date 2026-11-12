"""Simulator scores are shown as whole percents (73.125 -> 73%). The stored value keeps full precision."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def whole_number(value):
    """Round a score to a whole number string, or None when it isn't a usable number."""
    if value is None or value == "":
        return None
    try:
        score = Decimal(str(value).strip().rstrip("%"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not score.is_finite():
        return None
    return format(score.quantize(Decimal("1"), rounding=ROUND_HALF_UP), "f")


def whole_percent(value, missing="Unavailable"):
    number = whole_number(value)
    return missing if number is None else number + "%"
