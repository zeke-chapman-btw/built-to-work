"""Bounded, escaped presentation of existing task AuditLog snapshots."""
import uuid
from apps.core.models import AuditLog
from django.contrib.contenttypes.models import ContentType
from apps.staffing.models import StaffProfile
from .models import Task
from .task_access import events_for, require_task_access

FIELDS = {"title": "Title", "description": "Description", "assigned_staff_id": "Assignee", "event_id": "Event",
          "due_date": "Due date", "priority": "Priority", "status": "Status", "archived_at": "Archived at"}
ACTIONS = {"created": "Task created", "updated": "Task details updated", "reassigned": "Task reassigned",
           "status": "Status changed", "completed": "Task completed", "reopened": "Task reopened",
           "archived": "Task archived", "restored": "Task restored"}


def entries_for(user, task):
    require_task_access(user, task)
    return AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(Task), object_id=str(task.pk),
                                   action__startswith="operations.task.").select_related("actor").order_by("-timestamp", "-pk")


def present_history(user, entries):
    entries = list(entries)
    staff_ids, event_ids = set(), set()
    for entry in entries:
        for snapshot in (entry.old_data, entry.new_data):
            for key, target in (("assigned_staff_id", staff_ids), ("event_id", event_ids)):
                try:
                    target.add(uuid.UUID(str((snapshot or {}).get(key))))
                except (ValueError, TypeError, AttributeError):
                    pass
    staff = {str(p.pk): str(p) for p in StaffProfile.objects.filter(pk__in=staff_ids)}
    events = {str(e.pk): e.name for e in events_for(user).filter(pk__in=event_ids)}

    def value(key, raw):
        if raw in (None, ""):
            return "Not set"
        if key == "assigned_staff_id":
            return staff.get(str(raw), "Former staff profile")
        if key == "event_id":
            return events.get(str(raw), "Associated event (restricted)")
        if key == "status":
            return dict(Task.Status.choices).get(raw, str(raw))
        if key == "priority":
            return dict(Task.Priority.choices).get(raw, str(raw))
        return str(raw)

    rows = []
    for entry in entries:
        old, new = entry.old_data or {}, entry.new_data or {}
        changes = [(label, value(key, old.get(key)), value(key, new.get(key)))
                   for key, label in FIELDS.items() if old.get(key) != new.get(key)]
        rows.append({"entry": entry, "action": ACTIONS.get(entry.action.rsplit(".", 1)[-1], "Task changed"), "changes": changes})
    return rows
