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


def effective_event_ids(task):
    ids = set(task.event_links.values_list("event_id", flat=True))
    if task.event_id:
        ids.add(task.event_id)
    return ids


def snapshot(task):
    return {"linked_event_ids": sorted(str(pk) for pk in effective_event_ids(task)),
            "linked_participant_ids": sorted(str(pk) for pk in task.participant_links.values_list("participant_id", flat=True))}


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
    return {"events":events, "participants":participants}
