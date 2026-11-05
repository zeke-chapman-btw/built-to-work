from django import template
from apps.recruiting.preferences import current_preferences

register = template.Library()
register.simple_tag(current_preferences, name='current_preferences_for')
