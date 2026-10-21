"""Render contextual creation actions only for linkable records."""
from django import template
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import reverse
from apps.operations.quick_create import context_for
register = template.Library()
@register.simple_tag(takes_context=True)
def task_create_action(context, event=None, participant=None):
    request = context.get('request')
    if not request or not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return ''
    try:
        linked_event, person = context_for(request.user, event_id=event.pk if event else None, participant_id=participant.pk if participant else None)
    except (PermissionDenied, Http404):
        return ''
    if linked_event:
        return reverse('operations:quick-create-event', args=[linked_event.pk])
    if person:
        return reverse('operations:quick-create-participant', args=[person.pk])
    return ''


@register.simple_tag(takes_context=True)
def global_task_create_url(context):
    """Shared staff action; views may set task_creation_enabled=False."""
    from apps.operations.task_access import staff_user
    request = context.get('request')
    if not request or not staff_user(request.user) or context.get('task_creation_enabled') is False:
        return ''
    contextual = task_create_action(context, event=context.get('event'), participant=context.get('participant'))
    return contextual or context.get('quick_create_url') or reverse('operations:quick-create')
