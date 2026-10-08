from base64 import b64encode
from functools import wraps
from io import BytesIO
from zoneinfo import ZoneInfo

import qrcode
from qrcode.image.svg import SvgPathImage
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch, Q, Case, When, Value, IntegerField
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.participants.models import Participant
from apps.participants.identity import find_or_create_participant
from apps.stations.forms import StationForEventForm
from apps.stations.services import assign_station
from .forms import EventForm, RegistrationForm, ReissueTicketForm, VoidRegistrationForm
from .models import Attendance, Event, EventRegistration, QrTicket
from .services import audit_action, check_in_registration, register_participant, resolve_ticket, reissue_ticket, void_registration


def staff_required(view_func):
    @login_required(login_url="/admin/login/")
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped


def _ticket_qr_data(request, ticket):
    ticket_url = request.build_absolute_uri(reverse("events:ticket_present", kwargs={"token": ticket.token}))
    image = qrcode.make(ticket_url, image_factory=SvgPathImage)
    output = BytesIO()
    image.save(output)
    return "data:image/svg+xml;base64," + b64encode(output.getvalue()).decode("ascii")


def _event_snapshot(event):
    return {"name": event.name, "code": event.code, "status": event.status,
            "start_at": event.start_at.isoformat(), "end_at": event.end_at.isoformat(),
            "timezone_name": event.timezone_name, "location_name": event.location_name}


@staff_required
def event_list(request):
    events = Event.objects.annotate(
        registration_count=Count("registrations", filter=Q(registrations__status=EventRegistration.Status.ACTIVE), distinct=True),
        attendance_count=Count("attendance_records", filter=Q(attendance_records__is_void=False), distinct=True),
        status_order=Case(When(status=Event.Status.UPCOMING, then=Value(0)), When(status=Event.Status.DRAFT, then=Value(1)), default=Value(2), output_field=IntegerField()),
    ).order_by("status_order", "start_at", "name")
    return render(request, "events/event_list.html", {"events": events, "page_title": "Events"})


@staff_required
def event_create(request):
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.save()
        audit_action(actor=request.user, action="event.created", instance=event, new_data=_event_snapshot(event))
        messages.success(request, "Event created.")
        return redirect("events:event_detail", event_id=event.pk)
    return render(request, "events/event_form.html", {"form": form, "page_title": "Create event"})


@staff_required
def event_edit(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    form = EventForm(request.POST or None, instance=event)
    if request.method == "POST" and form.is_valid():
        old_data = _event_snapshot(event)
        event = form.save()
        audit_action(actor=request.user, action="event.updated", instance=event, old_data=old_data, new_data=_event_snapshot(event))
        messages.success(request, "Event updated.")
        return redirect("events:event_detail", event_id=event.pk)
    return render(request, "events/event_form.html", {"form": form, "event": event, "page_title": "Edit event"})


@staff_required
def event_detail(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    form = RegistrationForm(request.POST if request.method == "POST" and request.POST.get("action") == "register" else None)
    if request.method == "POST" and request.POST.get("action") == "register" and form.is_valid():
        data = form.cleaned_data
        participant = data["participant"]
        if participant is None:
            resolution = find_or_create_participant(
                first_name=data["first_name"], last_name=data["last_name"],
                email=data.get("contact_email"), phone=data.get("contact_phone"),
            )
            if resolution.status == "ambiguous":
                form.add_error(None, "Those contact details match different participants. Select the correct participant before registering.")
            else:
                participant = resolution.participant
        if participant is not None:
            try:
                registration, created = register_participant(event=event, participant=participant, actor=request.user, source=data["source"])
                messages.success(request, "Participant registered and QR ticket issued." if created else "This participant is already actively registered for this event.")
                if data["check_in_now"]:
                    _, checked_in = check_in_registration(registration=registration, actor=request.user, source=Attendance.Source.STAFF)
                    messages.success(request, "Participant checked in." if checked_in else "Participant was already checked in.")
                return redirect("events:registration_detail", event_id=event.pk, registration_id=registration.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
    registrations = EventRegistration.objects.filter(event=event, status=EventRegistration.Status.ACTIVE).select_related("participant").prefetch_related(
        Prefetch("tickets", queryset=QrTicket.objects.order_by("-issued_at"), to_attr="ticket_history"),
        Prefetch("attendance_records", queryset=Attendance.objects.order_by("-checked_in_at"), to_attr="attendance_history"),
    ).order_by("participant__last_name", "participant__first_name")
    resolution = None
    scan_form_token = request.POST.get("token", "") if request.method == "POST" and request.POST.get("action") == "scan" else ""
    if request.method == "POST" and request.POST.get("action") == "scan":
        resolution = resolve_ticket(scan_form_token, expected_event=event)
        if resolution.status == "valid":
            attendance, created = check_in_registration(registration=resolution.ticket.registration, actor=request.user, source=Attendance.Source.QR_SCAN)
            resolution = resolve_ticket(scan_form_token, expected_event=event)
            messages.success(request, "Check-in recorded." if created else "Participant was already checked in.")
        elif resolution.status == "already_checked_in":
            local_time = timezone.localtime(resolution.attendance.checked_in_at, ZoneInfo(event.timezone_name)).strftime("%I:%M %p").lstrip("0")
            messages.info(request, f"Already checked in at {local_time}.")
        elif resolution.status == "wrong_event":
            messages.error(request, f"This ticket belongs to {resolution.ticket.registration.event.name}, not {event.name}. No check-in was recorded.")
        else:
            messages.error(request, {"expired": "This ticket has expired.", "revoked": "This ticket was replaced and is no longer valid.", "registration_inactive": "This registration is no longer active.", "not_found": "No ticket was found for that code."}.get(resolution.status, "Ticket could not be accepted."))
    with timezone.override(ZoneInfo(event.timezone_name)):
        return render(request, "events/event_detail.html", {
            "page_title": "Events",
            "event": event, "registrations": registrations, "station_form": StationForEventForm(), "station_assignments": event.station_assignments.select_related("station"), "experience_sessions": event.experience_sessions.select_related("participant").prefetch_related("activities"),
            "registration_count": EventRegistration.objects.filter(event=event, status=EventRegistration.Status.ACTIVE).count(),
            "attendance_count": Attendance.objects.filter(event=event, is_void=False).count(),
            "registration_form": form, "scan_form_token": scan_form_token, "scan_resolution": resolution,
        })


@staff_required
def registration_detail(request, event_id, registration_id):
    event = get_object_or_404(Event, pk=event_id)
    registration = get_object_or_404(EventRegistration.objects.select_related("event", "participant", "created_by"), pk=registration_id, event=event)
    ticket = registration.tickets.filter(is_current=True).first()
    attendance = registration.attendance_records.filter(is_void=False).first()
    with timezone.override(ZoneInfo(registration.event.timezone_name)):
        return render(request, "events/registration_detail.html", {
            "page_title": "Events",
            "event": event, "registration": registration, "ticket": ticket,
            "ticket_qr_data": _ticket_qr_data(request, ticket) if ticket else None,
            "attendance": attendance,
            "reissue_form": ReissueTicketForm(initial={"expected_ticket_id": ticket.pk}) if ticket else None,
            "void_form": VoidRegistrationForm() if registration.status == EventRegistration.Status.ACTIVE else None,
        })


@staff_required
def registration_reissue(request, event_id, registration_id):
    event = get_object_or_404(Event, pk=event_id)
    registration = get_object_or_404(EventRegistration, pk=registration_id, event=event)
    if request.method != "POST":
        return HttpResponse(status=405)
    form = ReissueTicketForm(request.POST)
    if form.is_valid():
        _, changed = reissue_ticket(registration=registration, actor=request.user, reason=form.cleaned_data["reason"], expected_ticket_id=form.cleaned_data["expected_ticket_id"])
        messages.success(request, "A replacement ticket was issued." if changed else "Ticket is already current; no additional replacement was made.")
    else:
        messages.error(request, "The ticket request was invalid. Reload the page and try again.")
    return redirect("events:registration_detail", event_id=event.pk, registration_id=registration.pk)


@staff_required
def registration_void(request, event_id, registration_id):
    event = get_object_or_404(Event, pk=event_id)
    registration = get_object_or_404(EventRegistration, pk=registration_id, event=event)
    if request.method != "POST":
        return HttpResponse(status=405)
    form = VoidRegistrationForm(request.POST)
    if form.is_valid():
        try:
            changed = void_registration(registration=registration, actor=request.user, reason=form.cleaned_data["reason"])
            messages.success(request, "Registration voided and current ticket revoked." if changed else "Registration was already voided.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect("events:registration_detail", event_id=event.pk, registration_id=registration.pk)


@staff_required
def registration_check_in(request, event_id, registration_id):
    event = get_object_or_404(Event, pk=event_id)
    registration = get_object_or_404(EventRegistration, pk=registration_id, event=event)
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        attendance, created = check_in_registration(registration=registration, actor=request.user, source=Attendance.Source.STAFF)
        messages.success(request, "Check-in recorded." if created else f"Already checked in at {timezone.localtime(attendance.checked_in_at).strftime('%I:%M %p').lstrip('0')}.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("events:event_detail", event_id=event.pk)


def ticket_present(request, token):
    resolution = resolve_ticket(token)
    if resolution.status == "not_found":
        raise Http404
    if resolution.status not in ("valid", "already_checked_in"):
        return render(request, "events/ticket_unavailable.html", {"resolution": resolution}, status=410)
    ticket = resolution.ticket
    with timezone.override(ZoneInfo(ticket.registration.event.timezone_name)):
        return render(request, "events/ticket_present.html", {
            "page_title": "Events",
            "ticket": ticket, "event": ticket.registration.event,
            "ticket_qr_data": _ticket_qr_data(request, ticket),
            "already_checked_in": resolution.status == "already_checked_in",
        })


@staff_required
def event_station_assign(request, event_id):
    from apps.stations.models import EventStation
    from apps.stations.services import assign_station

    event = get_object_or_404(Event, pk=event_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    form = StationForEventForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid active station and assignment settings.")
        return redirect("events:event_detail", event_id=event.pk)
    assignment, created = assign_station(event=event, station=form.cleaned_data["station"], actor=request.user)
    old_data = {"enabled": assignment.enabled, "display_order": assignment.display_order}
    assignment.enabled = form.cleaned_data["enabled"]
    assignment.display_order = form.cleaned_data["display_order"]
    if not assignment.enabled:
        assignment.is_active_context = False
    assignment.save()
    if not created:
        from apps.events.services import audit_action
        audit_action(actor=request.user, action="station.event_assignment.updated", instance=assignment, old_data=old_data, new_data={"enabled": assignment.enabled, "display_order": assignment.display_order})
    messages.success(request, "Station assigned to event." if created else "Station assignment updated.")
    return redirect("events:event_detail", event_id=event.pk)
