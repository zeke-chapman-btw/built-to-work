from django import template
register = template.Library()
@register.filter
def lookup(mapping, key):
    return mapping.get(key) if mapping else None
