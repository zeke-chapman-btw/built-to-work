from django import template
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.operations.task_access import require_task_access
from apps.recruiting.access import policy_transaction
from apps.crm import access
from apps.crm.communications import contact_compose

register = template.Library()


@register.simple_tag
def crm_contact_email(contact):
    return contact_compose(contact)


@register.simple_tag
def crm_task_contexts(actor, task, request=None):
    """Task access never grants access to private relationship details."""
    result = []
    with policy_transaction():
        try:
            actor = access.fresh_staff(actor)
            require_task_access(actor, task)
        except PermissionDenied:
            return result
        for link in task.crm_links.select_related("interaction__participant", "interaction__organization", "interaction__engagement"):
            row = link.interaction
            try:
                access.internal_record(actor, row)
            except PermissionDenied:
                continue
            from apps.crm.navigation import with_origin, with_context, previous
            back = reverse("operations:task", args=[task.pk])
            if request and request.user.pk == actor.pk and previous(request):
                back = with_context(back, previous(request), "crm_return_to")
            result.append({"label": row.summary, "url": with_origin(reverse("crm:detail", args=[row.pk]), back),
                "participant": str(row.participant or ""), "organization": str(row.organization or "")})
    return result


@register.simple_tag
def crm_task_return(actor, task, request):
    """A linked Task does not authorize its private relationship destination."""
    from django.urls import resolve
    from apps.crm.navigation import previous
    from apps.participants.identity import canonical_id, identity_members
    try:
        actor = access.fresh_staff(actor)
        if actor.pk != request.user.pk:
            return None
        require_task_access(actor, task)
        target = previous(request)
        if not target:
            return None
        from urllib.parse import urlsplit
        match = resolve(urlsplit(target).path)
        if match.view_name == 'participants:staff-profile':
            ids = identity_members(canonical_id(match.kwargs['participant_id']))
            if task.participant_links.filter(participant_id__in=ids).exists():
                return {'url': target}
        for link in task.crm_links.select_related('interaction__participant', 'interaction__organization', 'interaction__engagement'):
            row = link.interaction
            try:
                access.internal_record(actor, row)
            except PermissionDenied:
                continue
            if (match.view_name == 'participants:staff-profile' and row.participant_id in ids
                    or match.view_name == 'customers:workspace' and str(row.organization_id) == str(match.kwargs['org_id'])
                    or match.view_name in {'crm:recent', 'operations:task'}):
                return {'url': target}
    except PermissionDenied:
        pass
    return None
