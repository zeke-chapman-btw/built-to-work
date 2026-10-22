"""Atomic, task-authorized links and comments using the existing AuditLog."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from apps.events.models import Event
from apps.events.services import audit_action
from apps.participants.models import Participant
from apps.staffing.services import revision
from .models import Task, TaskEventLink, TaskParticipantLink, TaskComment
from .task_access import require_task_staff, require_task_access, task_admin, events_for
from .task_links import snapshot, participants_for
from .collaboration_forms import TaskLinksForm, TaskCommentForm


def record(actor, task, action, before, after):
    audit_action(actor=actor, action="operations.task."+action, instance=task, old_data=before, new_data=after)


@transaction.atomic
def add_links(*, actor, task_id, data, event_query="", participant_query="", include_selected=False):
    require_task_staff(actor)
    initial = Task.objects.get(pk=task_id)
    require_task_access(actor, initial)
    from .execution_services import guard_managed_task
    guard_managed_task(initial)
    form = TaskLinksForm(data, actor=actor, event_query=event_query, participant_query=participant_query, include_selected=include_selected)
    if not form.is_valid():
        return None, form
    events = list(form.cleaned_data["events"])
    people = list(form.cleaned_data["participants"])
    list(Event.objects.select_for_update().filter(pk__in=[e.pk for e in events]).order_by("pk"))
    list(Participant.objects.select_for_update().filter(pk__in=[p.pk for p in people]).order_by("pk"))
    task = Task.objects.select_for_update().get(pk=task_id)
    require_task_access(actor, task)
    revision(task, form.cleaned_data["revision"])
    if events_for(actor).exclude(status="archived").filter(pk__in=[e.pk for e in events]).count()!=len(events):
        raise PermissionDenied
    if participants_for(actor).filter(kind="person",archived_at__isnull=True,pk__in=[p.pk for p in people]).count()!=len(people):
        raise PermissionDenied
    before = snapshot(task)
    for event in events:
        if event.pk != task.event_id:
            TaskEventLink.objects.get_or_create(task=task, event=event)
    for person in people:
        TaskParticipantLink.objects.get_or_create(task=task, participant=person)
    after = snapshot(task)
    if before != after:
        task.save(update_fields=["updated_at"])
        record(actor, task, "links.updated", before, after)
    return task, form


@transaction.atomic
def remove_link(*, actor, task_id, kind, link_id, data):
    from .forms import TaskRevisionForm
    require_task_staff(actor)
    task = Task.objects.select_for_update().get(pk=task_id)
    require_task_access(actor, task)
    from .execution_services import guard_managed_task
    guard_managed_task(task)
    form = TaskRevisionForm(data)
    if not form.is_valid():
        return None, form
    revision(task, form.cleaned_data["revision"])
    before = snapshot(task)
    if kind == "primary" and str(link_id)==str(task.pk) and task.event_id:
        TaskEventLink.objects.filter(task=task, event_id=task.event_id).delete()
        task.event = None  # Explicit authorized unlink only; never a migration backfill.
    elif kind in ("event", "participant"):
        model = TaskEventLink if kind=="event" else TaskParticipantLink
        link = model.objects.filter(task=task, pk=link_id).first()
        if not link:
            raise PermissionDenied
        link.delete()
    else:
        raise PermissionDenied
    task.save(update_fields=["event", "updated_at"])
    record(actor, task, "links.updated", before, snapshot(task))
    return task, form


def comment_snapshot(comment):
    return {"comment_id":str(comment.pk), "comment_text":comment.content,
            "comment_author_id":str(comment.author_id) if comment.author_id else None,
            "comment_created_at":comment.created_at.isoformat()}


@transaction.atomic
def save_comment(*, actor, task_id, data, comment_id=None):
    require_task_staff(actor)
    task = Task.objects.select_for_update().get(pk=task_id)
    require_task_access(actor, task)
    if task.archived_at:
        raise ValidationError("Restore this task before adding or editing comments.")
    comment = TaskComment.objects.select_for_update().get(pk=comment_id, task=task) if comment_id else TaskComment(task=task, author=actor)
    if comment_id and comment.author_id!=actor.pk and not task_admin(actor):
        raise PermissionDenied
    form = TaskCommentForm(data)
    if not form.is_valid():
        return None, form
    revision(comment if comment_id else task, form.cleaned_data["revision"])
    before = comment_snapshot(comment) if comment_id else {}
    if comment_id and comment.content == form.cleaned_data["content"]:
        return comment, form
    comment.content = form.cleaned_data["content"]
    if comment_id:
        comment.edited_by = actor
    comment.full_clean()
    comment.save()
    action = "comment.created" if not comment_id else ("comment.edited" if comment.author_id==actor.pk else "comment.moderated")
    record(actor, task, action, before, comment_snapshot(comment))
    return comment, form
