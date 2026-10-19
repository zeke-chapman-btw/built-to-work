"""Permission-controlled Event editing and explicit lifecycle transitions."""
from datetime import UTC, datetime
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .forms import EventActionForm, EventForm
from .models import Event
from .services import audit_action

TRANSITIONS = {
    "publish": ("Publish to Staff", {"draft"}, "upcoming"),
    "complete": ("Complete Event", {"upcoming"}, "completed"),
    "cancel": ("Cancel Event", {"draft", "upcoming"}, "cancelled"),
    "archive": ("Archive Event", {"draft", "completed", "cancelled"}, "archived"),
    "finalize_details": ("Finalize details", {"draft", "upcoming"}, None),
    "reopen_details": ("Reopen details", {"draft", "upcoming"}, None),
}

def require_event_permission(actor, permission):
    if not actor.is_authenticated or not actor.is_active or not actor.is_staff or not actor.has_perm(permission):
        raise PermissionDenied

def event_snapshot(event):
    fields = (*EventForm.Meta.fields, "status", "staff_published_at", "staff_published_by_id", "details_finalized_at", "details_finalized_by_id")
    result = {}
    for name in fields:
        value = getattr(event, name)
        if isinstance(value, datetime) and timezone.is_aware(value):
            value = value.astimezone(UTC)
        result[name] = value.isoformat() if hasattr(value, "isoformat") else str(value) if name.endswith("_id") and value is not None else value
    return result

def available_actions(event):
    actions = []
    for action, (label, states, target) in TRANSITIONS.items():
        if event.status not in states:
            continue
        if action == "finalize_details" and event.details_finalized_at:
            continue
        if action == "reopen_details" and not event.details_finalized_at:
            continue
        actions.append({"id": action, "label": label})
    return actions

@transaction.atomic
def save_event(*, data, actor, event_id=None):
    require_event_permission(actor, "events.change_event" if event_id else "events.add_event")
    event = Event.objects.select_for_update().get(pk=event_id) if event_id else Event()
    before = event_snapshot(event) if event_id else None
    action = data.get("action") or ("save" if event_id else "save_draft")
    if action == "publish":
        require_event_permission(actor, "events.change_event")
    form = EventForm(data, instance=event)
    if not form.is_valid():
        return None, form
    if action not in ("save", "save_draft", "publish"):
        form.add_error(None, "Unsupported event action.")
    if event_id and data.get("revision") != event.updated_at.isoformat():
        form.add_error(None, "This event changed after you opened it. Reload the event and review the latest values before saving.")
    if event.status == Event.Status.ARCHIVED:
        form.add_error(None, "Archived events are read-only.")
    if action in ("publish", "save_draft") and event.status != Event.Status.DRAFT:
        form.add_error(None, "Only a draft can be saved as a draft or published.")
    if form.errors:
        return None, form
    event = form.save(commit=False)
    if event_id:
        from apps.staffing.services import validate_event_schedule
        try:
            validate_event_schedule(event, before)
        except ValidationError as exc:
            form.add_error(None, exc)
            return None, form
    if not event_id:
        event.created_by = actor
    changed = before is None or any(before[name] != event_snapshot(event)[name] for name in EventForm.Meta.fields)
    if changed and event.details_finalized_at:
        event.details_finalized_at = None
        event.details_finalized_by = None
    if action == "publish":
        event.status = Event.Status.UPCOMING
        event.staff_published_at = timezone.now()
        event.staff_published_by = actor
    event.save()
    audit_action(actor=actor, action="event.updated" if event_id else "event.created", instance=event, old_data=before, new_data=event_snapshot(event))
    if action == "publish":
        audit_action(actor=actor, action="event.published", instance=event, old_data={"status": "draft"}, new_data={"status": "upcoming"}, reason="Published to staff; public registration settings retained.")
    return event, form

@transaction.atomic
def transition_event(*, event_id, action, actor, data):
    require_event_permission(actor, "events.change_event")
    event = Event.objects.select_for_update().get(pk=event_id)
    form = EventActionForm(data)
    if not form.is_valid():
        return None, form
    if action not in TRANSITIONS or action not in {item["id"] for item in available_actions(event)}:
        form.add_error(None, "This action is not available for the event's current state.")
    if form.cleaned_data["revision"] != event.updated_at.isoformat():
        form.add_error(None, "This event has changed. Reload it before confirming this action.")
    if action in ("cancel", "archive") and not form.cleaned_data["reason"].strip():
        form.add_error("reason", "Enter a reason to preserve the event history.")
    if form.errors:
        return None, form
    before = event_snapshot(event)
    target = TRANSITIONS[action][2]
    if target:
        event.status = target
    if action == "publish":
        event.staff_published_at = timezone.now()
        event.staff_published_by = actor
    elif action == "finalize_details":
        event.details_finalized_at = timezone.now()
        event.details_finalized_by = actor
    elif action == "reopen_details":
        event.details_finalized_at = None
        event.details_finalized_by = None
    event.full_clean()
    event.save()
    audit_action(actor=actor, action="event." + action, instance=event, old_data=before, new_data=event_snapshot(event), reason=form.cleaned_data["reason"].strip())
    return event, form
