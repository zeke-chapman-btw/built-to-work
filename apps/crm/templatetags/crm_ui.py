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
def crm_task_contexts(actor, task):
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
            result.append({"label": row.summary, "url": reverse("crm:detail", args=[row.pk]),
                "participant": str(row.participant or ""), "organization": str(row.organization or "")})
    return result
