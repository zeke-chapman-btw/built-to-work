"""Effective links are the legacy Event FK plus unique additional typed links."""
from django.db.models import Q
from django.urls import reverse
from apps.participants.models import Participant
from .models import TaskEventLink, TaskParticipantLink
from .task_access import require_task_staff, events_for, require_task_access


def participants_for(user):
    require_task_staff(user)
    # Same staff permission as the canonical directory/profile. Those views gate
    # qualifications/documents/results independently; linking never grants them.
    return Participant.objects.all() if user.has_perm("participants.view_participant") else Participant.objects.none()


def customers_for(user):
    require_task_staff(user)
    from apps.customers.access import organizations
    from apps.customers.models import CustomerOrganization
    from django.core.exceptions import PermissionDenied
    try:return organizations(user)
    except PermissionDenied:return CustomerOrganization.objects.none()


def record_label(kind, obj):
    if kind=='participants':return f'{obj} · ID {obj.pk}'
    if kind=='customers':return f'{obj.name} · {str(obj.pk)[:8]}'
    return f'{obj.name} · {obj.local_start_at.date().isoformat()} · {obj.code or str(obj.pk)[:8]}'


def effective_event_ids(task):
    ids = set(task.event_links.values_list("event_id", flat=True))
    if task.event_id:
        ids.add(task.event_id)
    return ids


def snapshot(task):
    return {"linked_event_ids": sorted(str(pk) for pk in effective_event_ids(task)),
            "linked_participant_ids": sorted(str(pk) for pk in task.participant_links.values_list("participant_id", flat=True)),
            "linked_customer_ids": sorted(str(pk) for pk in task.customer_links.values_list("organization_id",flat=True))}


def event_tasks(queryset, event):
    return queryset.filter(Q(event=event) | Q(event_links__event=event)).distinct()


def display_links(user, task):
    require_task_access(user, task)
    links = list(task.event_links.order_by("created_at", "pk"))
    event_ids = effective_event_ids(task)
    visible_events = {e.pk:e for e in events_for(user).filter(pk__in=event_ids)}
    events = []
    seen = set()
    if task.event_id:
        links.insert(0, None)
    for link in links:
        pk = link.event_id if link else task.event_id
        if pk in seen:
            continue
        seen.add(pk)
        event = visible_events.get(pk)
        url = reverse("events:event_detail", args=[pk]) if event and user.has_perm("events.view_event") else (reverse("staffing:staff-event", args=[pk]) if event else None)
        if event and not user.has_perm("events.view_event"):
            from .execution_models import EventExecution
            from apps.accounts.operational_access import safe_event_worker
            if not safe_event_worker(user) and EventExecution.objects.filter(event=event,primary_manager__user=user,primary_manager__is_active=True).exists():
                url=reverse("operations:execution",args=[pk])
        events.append({"label":event.name if event else "Restricted event", "url":url,
                       "primary":link is None, "kind":"event" if link else "primary", "link_id":link.pk if link else task.pk})
    links = list(task.participant_links.order_by("created_at", "pk"))
    visible = {p.pk:p for p in participants_for(user).filter(pk__in=[l.participant_id for l in links])}
    participants = []
    for link in links:
        person = visible.get(link.participant_id)
        participants.append({"label":str(person) if person else "Restricted participant",
            "url":reverse("participants:staff-profile", args=[person.pk]) if person else None,
            "kind":"participant", "link_id":link.pk})
    customers=[]
    links=list(task.customer_links.order_by('created_at','pk'))
    visible={row.pk:row for row in customers_for(user).filter(pk__in=[link.organization_id for link in links])}
    for link in links:
        company=visible.get(link.organization_id)
        customers.append({'label':record_label('customers',company) if company else 'Restricted customer',
          'url':reverse('customers:workspace',args=[company.pk,'overview']) if company else None,
          'kind':'customer','link_id':link.pk})
    return {"events":events, "participants":participants,'customers':customers}
