from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.events.models import Attendance, Event, EventRegistration
from apps.events.services import audit_action, check_in_registration, resolve_ticket
from .models import EventStation, ExperienceActivity, ExperienceSession, Station

ACTIVITY_ORDER = (ExperienceActivity.Activity.KIOSK, ExperienceActivity.Activity.DUCK, ExperienceActivity.Activity.SIMULATOR)
STATION_ACTIVITY = {
    Station.Type.KIOSK: ExperienceActivity.Activity.KIOSK,
    Station.Type.DUCK: ExperienceActivity.Activity.DUCK,
    Station.Type.SIMULATOR: ExperienceActivity.Activity.SIMULATOR,
}


def assign_station(*, event, station, actor):
    assignment, created = EventStation.objects.get_or_create(event=event, station=station)
    if created:
        audit_action(actor=actor, action="station.assigned_to_event", instance=assignment, new_data={"event_id": str(event.pk), "station_id": str(station.pk)})
    return assignment, created


def activate_event_context(*, assignment, actor):
    with transaction.atomic():
        assignment = EventStation.objects.select_for_update().select_related("event", "station").get(pk=assignment.pk)
        if not assignment.enabled or not assignment.station.is_active:
            raise ValidationError("Enable the event assignment and station before activating it.")
        previous = EventStation.objects.filter(station=assignment.station, is_active_context=True).exclude(pk=assignment.pk)
        previous_ids = list(previous.values_list("pk", flat=True))
        previous.update(is_active_context=False, updated_at=timezone.now())
        if not assignment.is_active_context:
            assignment.is_active_context = True
            assignment.save(update_fields=("is_active_context", "updated_at"))
            audit_action(actor=actor, action="station.event_context.activated", instance=assignment, old_data={"previous_assignment_ids": [str(pk) for pk in previous_ids]}, new_data={"event_id": str(assignment.event_id), "station_id": str(assignment.station_id)})
        return assignment


def _ensure_activity_rows(session):
    for activity in ACTIVITY_ORDER:
        ExperienceActivity.objects.get_or_create(session=session, activity=activity)


def start_test_session(*, event, mode, actor):
    if mode not in (ExperienceSession.Mode.STAFF_TEST, ExperienceSession.Mode.DEMO):
        raise ValidationError("Choose an explicit staff test or demo mode.")
    session = ExperienceSession.objects.create(event=event, mode=mode, created_by=actor)
    _ensure_activity_rows(session)
    audit_action(actor=actor, action="experience.session.started", instance=session, new_data={"event_id": str(event.pk), "mode": mode})
    return session


def _finish_if_complete(session, actor):
    rows = {row.activity: row for row in session.activities.all()}
    if all(rows.get(key) and rows[key].status in (ExperienceActivity.Status.COMPLETED, ExperienceActivity.Status.SKIPPED) for key in ACTIVITY_ORDER):
        if session.completed_at is None:
            session.completed_at = timezone.now()
            session.save(update_fields=("completed_at",))
            audit_action(actor=actor, action="experience.session.completed", instance=session, new_data={"completed_at": session.completed_at.isoformat()})
        return True
    return False


def _prior_steps_complete(session, activity):
    index = ACTIVITY_ORDER.index(activity)
    rows = {row.activity: row for row in session.activities.all()}
    for required in ACTIVITY_ORDER[:index]:
        row = rows.get(required)
        if not row or row.status not in (ExperienceActivity.Status.COMPLETED, ExperienceActivity.Status.SKIPPED):
            return required
    return None


def process_station_scan(*, station_code, token, actor):
    assignment = EventStation.objects.select_related("event", "station").filter(
        station__code=station_code, station__is_active=True, enabled=True, is_active_context=True,
    ).first()
    if assignment is None:
        return {"status": "station_unconfigured"}
    event = assignment.event
    now = timezone.now()
    if event.status != Event.Status.UPCOMING or now < event.start_at or now > event.end_at:
        return {"status": "event_not_open", "assignment": assignment}

    resolution = resolve_ticket(token, expected_event=event)
    if resolution.status == "valid":
        if not event.allow_station_auto_check_in:
            return {"status": "staff_check_in_required", "assignment": assignment}
        try:
            check_in_registration(registration=resolution.ticket.registration, actor=actor, source=Attendance.Source.STATION)
        except ValidationError:
            return {"status": "check_in_unavailable", "assignment": assignment}
        resolution = resolve_ticket(token, expected_event=event)
    if resolution.status == "wrong_event":
        return {"status": "wrong_event", "assignment": assignment}
    if resolution.status not in ("valid", "already_checked_in"):
        return {"status": resolution.status, "assignment": assignment}
    if resolution.ticket is None:
        return {"status": "not_found", "assignment": assignment}
    registration = resolution.ticket.registration
    if not Attendance.objects.filter(registration=registration, is_void=False).exists():
        return {"status": "staff_check_in_required", "assignment": assignment}
    try:
        with transaction.atomic():
            session, created = ExperienceSession.objects.get_or_create(
                registration=registration,
                defaults={"event": event, "participant": registration.participant, "mode": ExperienceSession.Mode.OFFICIAL, "created_by": actor},
            )
            if session.event_id != event.pk or session.participant_id != registration.participant_id:
                return {"status": "wrong_event", "assignment": assignment}
            _ensure_activity_rows(session)
            activity_name = STATION_ACTIVITY[assignment.station.station_type]
            activity = ExperienceActivity.objects.select_for_update().get(session=session, activity=activity_name)
            blocked = _prior_steps_complete(session, activity_name)
            if blocked:
                return {"status": "prior_activity_required", "assignment": assignment, "session": session, "activity": activity, "required_activity": blocked}
            if session.completed_at:
                return {"status": "session_complete", "assignment": assignment, "session": session, "activity": activity}
            if activity.status == ExperienceActivity.Status.PENDING:
                activity.status = ExperienceActivity.Status.IN_PROGRESS
                activity.started_at = now
                activity.save(update_fields=("status", "started_at"))
                audit_action(actor=actor, action="experience.activity.started", instance=activity, new_data={"activity": activity_name, "session_id": str(session.pk)})
            return {"status": "activity_in_progress" if activity.status == ExperienceActivity.Status.IN_PROGRESS else activity.status, "assignment": assignment, "session": session, "activity": activity, "created": created}
    except IntegrityError:
        session = ExperienceSession.objects.get(registration=registration)
        return {"status": "activity_in_progress", "assignment": assignment, "session": session}


def complete_activity(*, session, activity_name, actor):
    if activity_name not in ACTIVITY_ORDER:
        raise ValidationError("Unknown experience activity.")
    with transaction.atomic():
        session = ExperienceSession.objects.select_for_update().get(pk=session.pk)
        activity = ExperienceActivity.objects.select_for_update().get(session=session, activity=activity_name)
        if activity.status == ExperienceActivity.Status.COMPLETED:
            return activity, False
        if activity.status != ExperienceActivity.Status.IN_PROGRESS:
            raise ValidationError("Only an in-progress activity can be completed.")
        activity.status = ExperienceActivity.Status.COMPLETED
        activity.completed_at = timezone.now()
        activity.save(update_fields=("status", "completed_at"))
        audit_action(actor=actor, action="experience.activity.completed", instance=activity, new_data={"activity": activity_name, "session_id": str(session.pk)})
        _finish_if_complete(session, actor)
        return activity, True


def skip_activity(*, session, activity_name, actor, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to skip an activity.")
    if activity_name not in ACTIVITY_ORDER:
        raise ValidationError("Unknown experience activity.")
    with transaction.atomic():
        session = ExperienceSession.objects.select_for_update().get(pk=session.pk)
        activity = ExperienceActivity.objects.select_for_update().get(session=session, activity=activity_name)
        if activity.status == ExperienceActivity.Status.SKIPPED:
            return activity, False
        if activity.status == ExperienceActivity.Status.COMPLETED:
            raise ValidationError("A completed activity cannot be skipped.")
        blocked = _prior_steps_complete(session, activity_name)
        if blocked:
            raise ValidationError(f"Complete or skip {blocked} before skipping this activity.")
        activity.status = ExperienceActivity.Status.SKIPPED
        activity.skipped_at = timezone.now()
        activity.skipped_by = actor
        activity.skip_reason = reason[:500]
        activity.save(update_fields=("status", "skipped_at", "skipped_by", "skip_reason"))
        audit_action(actor=actor, action="experience.activity.skipped", instance=activity, new_data={"activity": activity_name, "session_id": str(session.pk)}, reason=reason)
        _finish_if_complete(session, actor)
        return activity, True
