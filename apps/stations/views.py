import os

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.events.models import Event
from apps.events.views import staff_required
from .kiosk_review import TEST_EVENT_CODE
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


def participant_kiosk_signup(request, station_code):
    """Show the intake QR when a signup destination is configured."""
    import base64
    import os
    from io import BytesIO

    station = Station.objects.get(code=station_code)
    signup_qr_data = ""
    signup_url = os.environ.get("PARTICIPANT_SIGNUP_URL", "").strip()
    if signup_url:
        import qrcode

        qr = qrcode.make(signup_url)
        image_buffer = BytesIO()
        qr.save(image_buffer, format="PNG")
        encoded = base64.b64encode(image_buffer.getvalue()).decode("ascii")
        signup_qr_data = f"data:image/png;base64,{encoded}"
    return render(request, "stations/kiosk_signup.html", {
        "station": station,
        "signup_qr_data": signup_qr_data,
    })


def participant_kiosk(request, station_code):
    # Anonymous participant-facing kiosk; staff tools live at the service URL.
    assignment = EventStation.objects.select_related("event", "station").filter(
        station__code=station_code,
        station__station_type=Station.Type.KIOSK,
        station__is_active=True,
        enabled=True,
        is_active_context=True,
    ).first()
    request.session.pop("participant_kiosk_session", None)
    request.session.pop("test_kiosk_session", None)
    friendly_error = None
    ticket_error_code = None
    first_name = ""
    already_complete = False
    if assignment is None:
        friendly_error = "This kiosk is not ready yet. Please ask a Built to Work staff member for help."
    elif request.method == "POST":
        token = request.POST.get("token", "").strip()
        status, session = "not_found", None
        if token:
            result = process_station_scan(station_code=station_code, token=token, actor=None)
            status = result.get("status")
            session = result.get("session")
        if status == "activity_in_progress" and session is not None:
            if session.mode == ExperienceSession.Mode.STAFF_TEST:
                request.session["test_kiosk_session"] = str(session.pk)
            else:
                request.session["participant_kiosk_session"] = str(session.pk)
            return redirect(
                "assessments:station_start",
                station_code=station_code,
                session_id=session.pk,
            )
        elif status in {"session_complete", "activity_complete", "already_complete", "completed"}:
            ticket_error_code = "already_complete"
            already_complete = True
            friendly_error = "You've already completed this assessment. Please ask a staff member if you need help."
        else:
            ticket_error_code = status
            friendly_error = {
                "wrong_event": "Please ask a Built to Work team member for help.",
                "expired": "Please try scanning your Built to Work QR ticket again.",
                "revoked": "Please try scanning your Built to Work QR ticket again.",
                "registration_inactive": "Please try scanning your Built to Work QR ticket again.",
                "prior_activity_required": "Please ask a Built to Work team member for help.",
                "check_in_unavailable": "Please ask a Built to Work team member for help.",
                "not_found": "Please try scanning your Built to Work QR ticket again.",
                "station_unconfigured": "This kiosk is not ready yet. Please ask a Built to Work team member for help.",
            }.get(status, "Please ask a Built to Work team member for help before continuing.")
    return render(request, "stations/kiosk_home.html", {
        "station_code": station_code,
        "signup_url": os.environ.get("PARTICIPANT_SIGNUP_URL", "").strip(),
        "friendly_error": friendly_error,
        "ticket_error_code": ticket_error_code,
        "first_name": first_name,
        "already_complete": already_complete,
        "setup_error": assignment is None,
        "event_name": assignment.event.name if assignment else None,
    })



def _test_event_assignment(station):
    return EventStation.objects.select_related("event").filter(station=station, enabled=True, event__code=TEST_EVENT_CODE).first()

def _test_assessment_ready(assignment):
    from apps.assessments.services import eligible_event_categories
    return assignment is not None and len(eligible_event_categories(assignment.event)) >= 2


def service_menu(request, station_code):
    station = get_object_or_404(Station, code=station_code, station_type=Station.Type.KIOSK, is_active=True)
    assignment = _test_event_assignment(station)
    if request.method == "POST" and request.POST.get("action") == "return":
        return redirect("stations:kiosk", station_code=station.code)
    return render(request, "stations/service_menu.html", {"station": station, "test_ready": _test_assessment_ready(assignment)})

def service_start_test(request, station_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    station = get_object_or_404(Station, code=station_code, station_type=Station.Type.KIOSK, is_active=True)
    assignment = _test_event_assignment(station)
    if not _test_assessment_ready(assignment):
        return render(request, "stations/service_menu.html", {"station": station, "test_ready": False, "service_error": "Test Mode needs two ready categories with published question sets. Please ask a team member to configure the development review event."}, status=503)
    session = start_test_session(event=assignment.event, mode=ExperienceSession.Mode.STAFF_TEST, actor=None)
    request.session.pop("participant_kiosk_session", None)
    request.session["test_kiosk_session"] = str(session.pk)
    return redirect("assessments:station_start", station_code=station.code, session_id=session.pk)

def service_start_new_test(request, station_code, session_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.session.get("test_kiosk_session") != str(session_id):
        raise Http404
    old_session = get_object_or_404(ExperienceSession, pk=session_id, mode=ExperienceSession.Mode.STAFF_TEST, participant__isnull=True, registration__isnull=True)
    station = get_object_or_404(Station, code=station_code, station_type=Station.Type.KIOSK, is_active=True)
    assignment = _test_event_assignment(station)
    if assignment is None or assignment.event_id != old_session.event_id:
        raise Http404
    from apps.participants.models import TestParticipantRun
    from apps.participants.system_test import reset_test_participant_run
    linked_run = TestParticipantRun.objects.filter(experience_session=old_session).first()
    if linked_run is not None:
        if linked_run.reset_at is not None:
            raise Http404
        session = reset_test_participant_run(run=linked_run, reason="Test Mode started a new run").experience_session
    else:
        session = start_test_session(event=old_session.event, mode=ExperienceSession.Mode.STAFF_TEST, actor=None)
    request.session["test_kiosk_session"] = str(session.pk)
    return redirect("assessments:station_start", station_code=station_code, session_id=session.pk)

def service_exit_test(request, station_code, session_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.session.get("test_kiosk_session") != str(session_id):
        raise Http404
    get_object_or_404(ExperienceSession, pk=session_id, mode=ExperienceSession.Mode.STAFF_TEST, participant__isnull=True, registration__isnull=True)
    request.session.pop("test_kiosk_session", None)
    return redirect("stations:kiosk", station_code=station_code)


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
