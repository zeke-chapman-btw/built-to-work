
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.events.models import Event
from apps.events.views import staff_required
from .forms import EventStationForm, ExperienceModeForm, SkipActivityForm, StationForEventForm, StationForm
from .models import EventStation, ExperienceActivity, ExperienceSession, Station
from .services import (
    ACTIVITY_ORDER, STATION_ACTIVITY, activate_event_context, assign_station,
    complete_activity, process_station_scan, skip_activity, start_test_session,
)


@staff_required
def station_list(request):
    stations = Station.objects.annotate(assignment_count=Count("event_assignments", distinct=True)).prefetch_related("event_assignments__event")
    return render(request, "stations/station_list.html", {"stations": stations, "page_title": "Stations"})


@staff_required
def station_create(request):
    form = StationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        station = form.save()
        messages.success(request, "Station created.")
        return redirect("stations:detail", station_id=station.pk)
    return render(request, "stations/station_form.html", {"form": form, "page_title": "Create station"})


@staff_required
def station_edit(request, station_id):
    station = get_object_or_404(Station, pk=station_id)
    form = StationForm(request.POST or None, instance=station)
    if request.method == "POST" and form.is_valid():
        station = form.save()
        if not station.is_active:
            EventStation.objects.filter(station=station, is_active_context=True).update(is_active_context=False, updated_at=timezone.now())
        messages.success(request, "Station updated.")
        return redirect("stations:detail", station_id=station.pk)
    return render(request, "stations/station_form.html", {"form": form, "station": station, "page_title": "Edit station"})


@staff_required
def station_detail(request, station_id):
    station = get_object_or_404(Station, pk=station_id)
    form = EventStationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        assignment, created = assign_station(event=form.cleaned_data["event"], station=station, actor=request.user)
        messages.success(request, "Station assigned to event." if created else "This station is already assigned to that event.")
        return redirect("stations:detail", station_id=station.pk)
    assignments = station.event_assignments.select_related("event").all()
    return render(request, "stations/station_detail.html", {"station": station, "assignments": assignments, "assignment_form": form, "page_title": "Stations"})


@staff_required
def station_assign(request, station_id):
    station = get_object_or_404(Station, pk=station_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    form = EventStationForm(request.POST)
    if form.is_valid():
        assignment, created = assign_station(event=form.cleaned_data["event"], station=station, actor=request.user)
        assignment.enabled = form.cleaned_data["enabled"]
        assignment.display_order = form.cleaned_data["display_order"]
        if not assignment.enabled:
            assignment.is_active_context = False
        assignment.save()
        messages.success(request, "Event assignment saved." if created else "Event assignment updated.")
    else:
        messages.error(request, "Choose a valid event and assignment configuration.")
    return redirect("stations:detail", station_id=station.pk)


@staff_required
def assignment_activate(request, assignment_id):
    assignment = get_object_or_404(EventStation.objects.select_related("station", "event"), pk=assignment_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        activate_event_context(assignment=assignment, actor=request.user)
        messages.success(request, f"{assignment.event.name} is now the active context for {assignment.station.name}.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("stations:detail", station_id=assignment.station_id)


def _participant_label(session):
    if session.mode != ExperienceSession.Mode.OFFICIAL or not session.participant_id:
        return session.get_mode_display()
    person = session.participant
    first = person.preferred_name or person.first_name
    initial = f" {person.last_name[:1]}." if person.last_name else ""
    return f"{first}{initial}"


def _operator_context(assignment, *, result=None, scan_form=None, mode_form=None, skip_forms=None):
    session = result.get("session") if result else None
    activities = session.activities.all() if session else []
    current = None
    if session:
        current = next((item for item in activities if item.status in (ExperienceActivity.Status.IN_PROGRESS, ExperienceActivity.Status.PENDING)), None)
    safe_status = result.get("status") if result else None
    return {
        "assignment": assignment, "station": assignment.station, "event": assignment.event,
        "session": session, "activities": activities, "current_activity": current,
        "participant_label": _participant_label(session) if session else None,
        "scan_form": scan_form, "mode_form": mode_form or ExperienceModeForm(),
        "skip_forms": skip_forms or {}, "result_status": safe_status,
        "required_activity": result.get("required_activity") if result else None,
    }


@staff_required
def station_operate(request, station_code):
    assignment = EventStation.objects.select_related("station", "event").filter(station__code=station_code, station__is_active=True, enabled=True, is_active_context=True).first()
    if assignment is None:
        return render(request, "stations/station_unconfigured.html", {"station_code": station_code}, status=404)
    result = None
    scan_form = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "scan":
            token = request.POST.get("token", "").strip()
            result = process_station_scan(station_code=station_code, token=token, actor=request.user)
            status = result.get("status")
            if status == "activity_in_progress":
                messages.success(request, "Ticket accepted. Experience step is ready.")
            elif status in ("valid", "already_checked_in", "session_complete"):
                messages.info(request, "Participant experience is already complete." if status == "session_complete" else "This experience step is already recorded.")
            else:
                messages.error(request, {
                    "station_unconfigured": "This station has no active event context.",
                    "event_not_open": "This event is not currently open for station activity.",
                    "wrong_event": "This ticket belongs to a different event. No experience was recorded.",
                    "staff_check_in_required": "Check in this participant at the event desk first.",
                    "check_in_unavailable": "Check-in could not be recorded. Contact event staff.",
                    "revoked": "This ticket was replaced and is no longer valid.",
                    "expired": "This ticket has expired.",
                    "registration_inactive": "This registration is no longer active.",
                    "not_found": "No current ticket was found for that code.",
                    "prior_activity_required": "Complete the previous experience step first.",
                }.get(status, "The ticket could not be accepted."))
        elif action == "start_test":
            form = ExperienceModeForm(request.POST)
            if form.is_valid():
                session = start_test_session(event=assignment.event, mode=form.cleaned_data["mode"], actor=request.user)
                messages.success(request, "Staff test session started." if session.mode == ExperienceSession.Mode.STAFF_TEST else "Demo session started.")
                return redirect("stations:session_detail", session_id=session.pk)
        elif action == "complete":
            session = get_object_or_404(ExperienceSession, pk=request.POST.get("session_id"), event=assignment.event)
            activity_name = request.POST.get("activity")
            if activity_name != STATION_ACTIVITY[assignment.station.station_type]:
                raise PermissionDenied
            try:
                _, changed = complete_activity(session=session, activity_name=activity_name, actor=request.user)
                messages.success(request, "Experience step completed." if changed else "This step was already complete.")
                return redirect("stations:session_detail", session_id=session.pk)
            except (ValidationError, ExperienceActivity.DoesNotExist) as exc:
                messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else "The experience step is not available.")
                result = {"session": session, "status": "activity_in_progress"}
        elif action == "skip":
            session = get_object_or_404(ExperienceSession, pk=request.POST.get("session_id"), event=assignment.event)
            activity_name = request.POST.get("activity")
            if activity_name != STATION_ACTIVITY[assignment.station.station_type]:
                raise PermissionDenied
            form = SkipActivityForm(request.POST)
            try:
                if form.is_valid():
                    _, changed = skip_activity(session=session, activity_name=activity_name, actor=request.user, reason=form.cleaned_data["reason"])
                    messages.success(request, "Experience step skipped with an audit record." if changed else "This step was already skipped.")
                    return redirect("stations:session_detail", session_id=session.pk)
                messages.error(request, "Enter a reason to skip this step.")
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            result = {"session": session, "status": "activity_in_progress"}
    context = _operator_context(assignment, result=result, scan_form=scan_form)
    return render(request, "stations/operator.html", context)


@staff_required
def session_detail(request, session_id):
    session = get_object_or_404(ExperienceSession.objects.select_related("event", "participant", "registration"), pk=session_id)
    assignment = EventStation.objects.select_related("station", "event").filter(event=session.event, enabled=True, is_active_context=True).first()
    if assignment is None:
        return render(request, "stations/station_unconfigured.html", {"station_code": ""}, status=404)
    skip_forms = {row.activity: SkipActivityForm() for row in session.activities.all()}
    return render(request, "stations/operator.html", _operator_context(assignment, result={"session": session, "status": "session_complete" if session.completed_at else "activity_in_progress"}, skip_forms=skip_forms))
