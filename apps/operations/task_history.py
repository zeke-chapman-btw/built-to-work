"""One chronological, permission-filtered timeline backed by existing AuditLog."""
import uuid
from django.db.models import Q
from apps.core.models import AuditLog
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from apps.staffing.models import StaffProfile
from .models import Task, TaskComment
from .task_access import events_for, require_task_access, task_admin
from .task_links import participants_for

FIELDS = {"title":"Title", "description":"Description", "assigned_staff_id":"Assignee", "event_id":"Event",
          "due_date":"Due date", "priority":"Priority", "status":"Status", "archived_at":"Archived at",
          "linked_event_ids":"Linked events", "linked_participant_ids":"Linked participants", "comment_text":"Comment"}
ACTIONS = {"created":"Task created", "updated":"Task details updated", "reassigned":"Task reassigned",
           "status":"Status changed", "completed":"Task completed", "reopened":"Task reopened",
           "archived":"Task archived", "restored":"Task restored", "links.updated":"Linked records changed",
           "comment.created":"Comment added", "comment.edited":"Comment edited", "comment.moderated":"Comment moderated"}


def entries_for(user, task):
    require_task_access(user, task)
    return AuditLog.objects.filter(Q(action__startswith="operations.task.")|Q(action__startswith="event.execution.task."),content_type=ContentType.objects.get_for_model(Task), object_id=str(task.pk)).select_related("actor").order_by("timestamp", "pk")


def valid_uuid(raw):
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def present_history(user, entries):
    entries = list(entries)
    staff_ids, event_ids, participant_ids, comment_ids = set(), set(), set(), set()
    for entry in entries:
        for data in (entry.old_data or {}, entry.new_data or {}):
            for key, target in (("assigned_staff_id",staff_ids),("event_id",event_ids),("comment_id",comment_ids)):
                pk=valid_uuid(data.get(key))
                if pk:
                    target.add(pk)
            for raw in data.get("linked_event_ids",[]):
                pk=valid_uuid(raw)
                if pk:
                    event_ids.add(pk)
            for raw in data.get("linked_participant_ids",[]):
                if str(raw).isdigit():
                    participant_ids.add(int(raw))
    staff={str(p.pk):str(p) for p in StaffProfile.objects.filter(pk__in=staff_ids)}
    events={str(e.pk):e.name for e in events_for(user).filter(pk__in=event_ids)}
    people={str(p.pk):str(p) for p in participants_for(user).filter(pk__in=participant_ids)}
    comments={str(c.pk):c for c in TaskComment.objects.filter(pk__in=comment_ids,task_id__in=[e.object_id for e in entries]).select_related("task")}

    def value(key,raw):
        if raw in (None, "", []):
            return "Not set"
        if key=="assigned_staff_id":
            return staff.get(str(raw),"Former staff profile")
        if key=="event_id":
            return events.get(str(raw),"Restricted event")
        if key=="linked_event_ids":
            return "; ".join(events.get(str(pk),"Restricted event") for pk in raw)
        if key=="linked_participant_ids":
            return "; ".join(people.get(str(pk),"Restricted participant") for pk in raw)
        if key=="status":
            return dict(Task.Status.choices).get(raw,str(raw))
        if key=="priority":
            return dict(Task.Priority.choices).get(raw,str(raw))
        return str(raw)

    rows=[]
    for entry in entries:
        old,new=entry.old_data or {},entry.new_data or {}
        changes=[(label,value(key,old.get(key)),value(key,new.get(key))) for key,label in FIELDS.items()
                 if (old.get(key) or None)!=(new.get(key) or None) and not (key=="event_id" and ("linked_event_ids" in old or "linked_event_ids" in new))]
        action=entry.action.removeprefix("operations.task.").removeprefix("event.execution.task.")
        comment=comments.get(str(new.get("comment_id")))
        edit_url=None
        if comment and str(comment.task_id)==entry.object_id and not comment.task.archived_at and (comment.author_id==user.pk or task_admin(user)):
            edit_url=reverse("operations:comment-edit",args=[comment.task_id,comment.pk])
        rows.append({"entry":entry,"action":ACTIONS.get(action,"Task changed"),"changes":changes,
                     "comment_created":action=="comment.created", "comment_edit_url":edit_url})
    return rows
