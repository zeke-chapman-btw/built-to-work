"""One task policy: creator, current assignee, or explicit task administrator."""
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from apps.events.models import Event
from apps.staffing.models import StaffProfile
from .models import Task


def staff_user(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff)


def require_task_staff(user):
    if not staff_user(user):
        raise PermissionDenied


def task_admin(user):
    # Existing permission explicitly grants management of ALL operational tasks.
    return staff_user(user) and user.has_perm("operations.manage_tasks")


def tasks_for(user):
    require_task_staff(user)
    qs = Task.objects.select_related("event", "assigned_staff", "created_by", "completed_by", "archived_by")
    if task_admin(user):
        return qs
    # Legacy unlinked profiles do not grant access; missing creators stay missing.
    # A linked current assignee retains access even if their profile is retired.
    return qs.filter(Q(created_by=user) | Q(assigned_staff__user=user))


def require_task_access(user, task):
    if not tasks_for(user).filter(pk=task.pk).exists():
        raise PermissionDenied


def eligible_assignees():
    return StaffProfile.objects.filter(is_active=True, user__is_active=True, user__is_staff=True)


def events_for(user):
    require_task_staff(user)
    if user.has_perm("events.view_event"):
        return Event.objects.all()
    return Event.objects.filter(staff_assignments__staff__user=user, staff_assignments__staff__is_active=True,
                                staff_assignments__status="confirmed").exclude(status__in=("draft", "archived", "cancelled")).distinct()


def present_tasks(user, rows):
    rows = list(rows)
    ids = {row.event_id for row in rows if row.event_id}
    visible = set(events_for(user).filter(pk__in=ids).values_list("pk", flat=True))
    for row in rows:
        row.event_label = row.event.name if row.event_id in visible else ("Associated event (restricted)" if row.event_id else "Standalone task")
    return rows
