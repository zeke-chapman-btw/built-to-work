from functools import wraps
import uuid
from base64 import b64encode
from functools import wraps
from io import BytesIO
from zoneinfo import ZoneInfo

import qrcode
from qrcode.image.svg import SvgPathImage
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
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
from .models import Event, EventRegistration, QrTicket
from .services import audit_action, register_participant, resolve_ticket, reissue_ticket, void_registration, transfer_registration_group


def staff_required(view_func):
    @login_required(login_url="/admin/login/")
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped




def staff_permission_required(*permissions):
    """Require active staff and every listed Django model permission."""
    def decorate(view_func):
        @login_required(login_url="/admin/login/")
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_active or not user.is_staff:
                raise PermissionDenied
            if not all(user.has_perm(permission) for permission in permissions):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorate


def _ticket_qr_data(request, ticket):
    ticket_url = request.build_absolute_uri(reverse("events:ticket_present", kwargs={"token": ticket.token}))
    image = qrcode.make(f"tel:{ticket.ticket_number}" if ticket.ticket_number else ticket_url, image_factory=SvgPathImage)
    output = BytesIO()
    image.save(output)
    return "data:image/svg+xml;base64," + b64encode(output.getvalue()).decode("ascii")


def _event_snapshot(event):
    return {"name": event.name, "code": event.code, "status": event.status,
            "start_at": event.start_at.isoformat(), "end_at": event.end_at.isoformat(),
            "timezone_name": event.timezone_name, "location_name": event.location_name}


@staff_permission_required('events.view_event')
def event_list(request):
    from datetime import UTC, date, datetime, time, timedelta
    from django.core.paginator import Paginator
    from .forms import EventFilterForm
    data = request.GET.copy()
    data.setdefault("view", "upcoming")
    filters = EventFilterForm(data)
    events = Event.objects.all()
    selected = data.get("view", "upcoming")
    valid = filters.is_valid()
    if valid:
        values = filters.cleaned_data
        selected = values["view"]
        now = timezone.now()
        if selected == "current":
            events = events.filter(status="upcoming", start_at__lte=now, end_at__gte=now)
        elif selected == "upcoming":
            events = events.filter(status="upcoming", start_at__gt=now)
        elif selected != "all":
            events = events.filter(status=selected)
        if values["q"]:
            q = values["q"]
            events = events.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(location_name__icontains=q) | Q(city__icontains=q) | Q(event_type_other__icontains=q))
        if values["event_type"]:
            events = events.filter(event_type=values["event_type"])
        if values["date_from"]:
            events = events.filter(end_at__gte=datetime.combine(values["date_from"], time.min, tzinfo=UTC))
        if values["date_to"] and values["date_to"] < date.max:
            events = events.filter(start_at__lt=datetime.combine(values["date_to"] + timedelta(days=1), time.min, tzinfo=UTC))
    else:
        events = events.none()
    events = events.order_by("-start_at" if selected in ("completed", "cancelled", "archived") else "start_at", "name", "pk")
    can_view_staffing = request.user.has_perm("staffing.manage_event_staffing")
    if can_view_staffing:
        from django.db.models import Count
        events = events.select_related("staffing_plan").annotate(confirmed_staff=Count(
            "staff_assignments", filter=Q(staff_assignments__status="confirmed"), distinct=True))
    page = Paginator(events, 20).get_page(request.GET.get("page"))
    parameters = request.GET.copy(); parameters.pop("page", None)
    tabs = []
    for value, label in EventFilterForm.base_fields["view"].choices:
        query = parameters.copy(); query["view"] = value
        tabs.append({"id": value, "label": label, "url": "?" + query.urlencode()})
    return render(request, "events/event_list.html", {"events": page, "page_obj": page, "filters": filters,
        "can_view_staffing": can_view_staffing, "view_tabs": tabs, "selected_view": selected, "view_label": dict(EventFilterForm.base_fields["view"].choices).get(selected, "Events"),
        "page_query": parameters.urlencode(), "page_title": "Events", "page_description": "Plan, publish and manage event operations."}, status=200 if valid else 400)


@staff_permission_required('events.add_event')
def event_create(request):
    from .event_management import save_event
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)
    form = EventForm()
    if request.method == "POST":
        event, form = save_event(data=request.POST, actor=request.user)
        if event:
            messages.success(request, "Event published to staff." if event.status == "upcoming" else "Event saved as a draft.")
            return redirect("events:event_detail", event_id=event.pk)
    return render(request, "events/event_form.html", {"form": form, "page_title": "Create event"}, status=400 if form.is_bound else 200)


@staff_permission_required('events.change_event')
def event_edit(request, event_id):
    from .event_management import save_event
    event = get_object_or_404(Event, pk=event_id)
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)
    form = EventForm(instance=event)
    if request.method == "POST":
        saved, form = save_event(data=request.POST, actor=request.user, event_id=event.pk)
        if saved:
            messages.success(request, "Event saved. Changed details require finalization again." if saved.details_finalized_at is None else "Event saved.")
            return redirect("events:event_detail", event_id=event.pk)
    return render(request, "events/event_form.html", {"form": form, "event": event, "page_title": "Edit event"}, status=400 if form.is_bound else 200)


@staff_permission_required('events.change_event')
def event_action(request, event_id, action):
    from .event_management import TRANSITIONS, transition_event, available_actions
    from .forms import EventActionForm
    event = get_object_or_404(Event, pk=event_id)
    if action not in TRANSITIONS:
        raise Http404
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)
    if request.method == "GET" and action not in {item["id"] for item in available_actions(event)}:
        return HttpResponse("This action is not available for the event's current state.", status=409)
    form = EventActionForm(initial={"revision": event.updated_at.isoformat()})
    if request.method == "POST":
        try:
            changed, form = transition_event(event_id=event.pk, action=action, actor=request.user, data=request.POST)
        except ValidationError as exc:
            form = EventActionForm(request.POST); form.is_valid(); form.add_error(None, exc)
            changed = None
        if changed:
            messages.success(request, TRANSITIONS[action][0] + " completed.")
            return redirect("events:event_detail", event_id=event.pk)
        event.refresh_from_db()
    if action=="complete":
        from django import forms
        from apps.operations.execution_forms import eligible_people
        from apps.operations.execution_models import EventExecution
        execution=EventExecution.objects.filter(event=event).first()
        if not execution or not execution.primary_manager_id:
            form.fields["primary_manager"]=forms.ModelChoiceField(queryset=eligible_people(),label="Primary Event Manager",help_text="The closeout task will be assigned to this person.")
    return render(request, "events/event_action.html", {"event": event, "form": form, "action_id": action,
        "action_label": TRANSITIONS[action][0], "page_title": TRANSITIONS[action][0]}, status=400 if form.is_bound else 200)


@staff_permission_required('events.view_event')
def event_detail(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    section = request.GET.get("section", "overview")
    if section in ("tasks","notes","documents"):
        from apps.operations.services import require_manager
        require_manager(request.user,{"tasks":"tasks","notes":"event_notes","documents":"event_documents"}[section])
        return redirect("operations:event",event_id=event.pk,section=section)
    if section == "staffing":
        if not request.user.has_perm("staffing.manage_event_staffing"):
            raise PermissionDenied
        return redirect("staffing:event", event_id=event.pk)
    if section not in ("overview", "operations", "results", "registrations"):
        section = "overview"
    if request.method == "POST":
        section = "registrations"
    can_view_records = request.user.has_perms(("events.view_eventregistration", "participants.view_participant"))
    can_register = can_view_records and request.user.has_perm("events.add_eventregistration")
    form = RegistrationForm(request.POST if request.method == "POST" else None, event=event) if can_register and section == "registrations" else None
    if request.method == "POST":
        if request.POST.get("action") != "register":
            return HttpResponse("Unsupported event action.", status=400)
        if not can_register:
            raise PermissionDenied
        if form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    participant = data["participant"]
                    if participant is None:
                        if not request.user.has_perm("participants.add_participant"):
                            raise PermissionDenied
                        resolution = find_or_create_participant(first_name=data["first_name"], last_name=data["last_name"],
                            email=data.get("contact_email"), phone=data.get("contact_phone"))
                        if resolution.participant is None:
                            raise ValidationError("Participant identity needs staff review. Select the correct existing participant.")
                        participant = resolution.participant
                    registration, created = register_participant(event=event, participant=participant, actor=request.user,
                        source=data["source"], group=data.get("group"), override_reason=data.get("override_reason", ""))
                messages.success(request, "Participant registered and QR ticket issued." if created else "This participant is already actively registered for this event.")
                return redirect("events:registration_detail", event_id=event.pk, registration_id=registration.pk)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
    elif request.method != "GET":
        return HttpResponse(status=405)
    registrations = EventRegistration.objects.filter(event=event, status="active").select_related("participant").order_by("participant__last_name", "participant__first_name") if can_view_records and section == "registrations" else EventRegistration.objects.none()
    from .experience_operations import can_view_event_operations, event_experience_overview
    can_view_operations = can_view_event_operations(request.user)
    can_view_offline = request.user.has_perms(("events.view_offlineeventpreparation", "events.view_syncoutboxitem", "events.view_centraleventdownload"))
    overview = event_experience_overview(event, include_offline=can_view_offline) if can_view_operations and section == "results" else None
    from .event_management import available_actions
    with timezone.override(ZoneInfo(event.timezone_name)):
        return render(request, "events/event_detail.html", {
            "page_title": event.name, "page_description": "Event configuration and recorded experience progress.",
            "section": section, "management_actions": available_actions(event), "event": event, "registrations": registrations, "registration_form": form,
            "can_view_records": can_view_records, "can_view_operations": can_view_operations, "overview": overview,
            "station_form": StationForEventForm(), "station_assignments": event.station_assignments.select_related("station"),
            "experience_sessions": overview["sessions"] if overview else [],
            "registration_count": EventRegistration.objects.filter(event=event, status="active").values("participant_id").distinct().count() if can_view_records else None,
        }, status=400 if form is not None and form.is_bound and form.errors else 200)

@staff_permission_required('events.view_eventregistration')
def registration_detail(request, event_id, registration_id):
    event = get_object_or_404(Event, pk=event_id)
    registration = get_object_or_404(EventRegistration.objects.select_related("event", "participant", "created_by"), pk=registration_id, event=event)
    ticket = registration.tickets.filter(is_current=True).first()
    with timezone.override(ZoneInfo(registration.event.timezone_name)):
        return render(request, "events/registration_detail.html", {
            "page_title": "Events",
            "event": event, "registration": registration, "ticket": ticket,
            "ticket_qr_data": _ticket_qr_data(request, ticket) if ticket else None,
            "reissue_form": ReissueTicketForm(initial={"expected_ticket_id": ticket.pk}) if ticket else None,
            "void_form": VoidRegistrationForm() if registration.status == EventRegistration.Status.ACTIVE else None,
        })


@staff_permission_required('events.change_eventregistration')
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


@staff_permission_required("events.change_eventregistration")
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


def ticket_image(request, token):
    from .ticket_renderer import ticket_png_bytes
    resolution = resolve_ticket(token)
    if resolution.status == "not_found":
        raise Http404
    if resolution.ticket is None:
        raise Http404
    ticket = resolution.ticket
    if not ticket.ticket_number:
        return HttpResponse("This legacy ticket needs staff assistance.", status=409)
    response = HttpResponse(ticket_png_bytes(ticket), content_type="image/png")
    response["Content-Disposition"] = f'inline; filename="ticket-{ticket.ticket_number or ticket.token}.png"'
    return response

@staff_permission_required('events.change_event')
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


@staff_permission_required('events.view_qrticket', 'events.view_eventregistration')
def ticket_lookup(request, ticket_number):
    from .models import QrTicket
    ticket = get_object_or_404(QrTicket, ticket_number=ticket_number, is_current=True)
    return redirect("events:ticket_present", token=ticket.token)


@staff_permission_required('events.view_qrticket', 'events.view_eventregistration')
def ticket_recovery(request):
    """Staff-only lookup; never creates or changes a ticket."""
    from .models import QrTicket
    query = request.GET.get('q', '').strip()[:120]
    event_id = request.GET.get('event', '').strip()
    tickets = QrTicket.objects.none()
    if query or event_id:
        tickets = QrTicket.objects.select_related('registration__participant', 'registration__event').filter(is_current=True)
        if event_id:
            try:
                event = Event.objects.filter(pk=uuid.UUID(event_id)).first()
            except ValueError:
                event = None
            tickets = tickets.filter(registration__event=event) if event else QrTicket.objects.none()
        if query:
            tickets = tickets.filter(
                Q(ticket_number__icontains=query) |
                Q(registration__participant__first_name__icontains=query) |
                Q(registration__participant__last_name__icontains=query) |
                Q(registration__participant__preferred_name__icontains=query) |
                Q(registration__participant__contact_phone__icontains=query) |
                Q(registration__participant__contact_email__icontains=query)
            )
        tickets = list(tickets.order_by('-issued_at')[:50])
        for ticket in tickets:
            audit_action(actor=request.user, action='ticket.recovery.viewed', instance=ticket)
    return render(request, 'events/ticket_recovery.html', {
        'page_title': 'Ticket recovery', 'tickets': tickets, 'query': query,
        'event_id': event_id, 'events': Event.objects.order_by('-start_at')[:100],
    })


@staff_permission_required('events.change_qrticket')
def ticket_reprint(request, ticket_id):
    if request.method != 'POST':
        return HttpResponse(status=405)
    ticket = get_object_or_404(QrTicket, pk=ticket_id, is_current=True)
    audit_action(actor=request.user, action='ticket.recovery.reprinted', instance=ticket)
    return redirect('events:ticket_image', token=ticket.token)


@staff_permission_required('events.change_eventregistration')
def registration_group_change(request, event_id, registration_id):
    if request.method != "POST": return HttpResponse(status=405)
    if not request.user.has_perm("events.change_eventregistration"): raise PermissionDenied
    registration = get_object_or_404(EventRegistration, pk=registration_id, event_id=event_id)
    group_id = request.POST.get("group", "").strip()
    group = get_object_or_404(EventGroup, pk=group_id, event_id=event_id) if group_id else None
    try:
        _, changed = transfer_registration_group(registration=registration, destination=group, actor=request.user, override_reason=request.POST.get("override_reason", "").strip())
        messages.success(request, "Group assignment updated." if changed else "Group assignment is unchanged.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("events:registration_detail", event_id=event_id, registration_id=registration_id)

@staff_permission_required('events.change_eventregistration')
def registration_answers_correct(request, event_id, registration_id):
    if request.method != "POST": return HttpResponse(status=405)
    if not request.user.has_perm("events.change_eventregistration"): raise PermissionDenied
    registration = get_object_or_404(EventRegistration, pk=registration_id, event_id=event_id)
    answers = {key[7:]: value.strip() for key, value in request.POST.items() if key.startswith("answer_")}
    try:
        correct_registration_answers(registration=registration, answers=answers, actor=request.user, reason=request.POST.get("reason", ""))
        messages.success(request, "Event-specific answers updated.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("events:registration_detail", event_id=event_id, registration_id=registration_id)

@staff_permission_required('events.view_qrticket', 'events.view_eventregistration')
def bulk_tickets(request, event_id):
    if not request.user.has_perm("events.view_eventregistration"): raise PermissionDenied
    from .ticket_renderer import bulk_ticket_pdf_bytes
    event = get_object_or_404(Event, pk=event_id)
    registrations = EventRegistration.objects.filter(event=event, status=EventRegistration.Status.ACTIVE).select_related("participant", "group")
    if request.method == "GET":
        return render(request, "events/bulk_tickets.html", {"event": event, "registrations": registrations, "groups": event.groups.order_by("name"), "page_title": "Bulk tickets"})
    if request.method != "POST": return HttpResponse(status=405)
    scope = request.POST.get("scope", "")
    if scope == "group":
        group = get_object_or_404(EventGroup, pk=request.POST.get("group", ""), event=event)
        registrations = registrations.filter(group=group)
    elif scope == "selected":
        ids = request.POST.getlist("registrations")
        if not ids or len(ids) > 1000: return HttpResponse("Select one or more participants.", status=400)
        registrations = registrations.filter(pk__in=ids)
    elif scope != "all": return HttpResponse("Choose a print scope.", status=400)
    tickets = list(QrTicket.objects.filter(registration__in=registrations, is_current=True).select_related("registration__participant", "registration__event", "registration__group").order_by("registration__participant__last_name", "registration__participant__first_name"))
    if not tickets: return HttpResponse("No current tickets are available for this selection.", status=400)
    audit_action(actor=request.user, action="event.tickets.bulk_reprinted", instance=event, new_data={"scope": scope, "count": len(tickets), "ticket_ids": [str(ticket.pk) for ticket in tickets]})
    response = HttpResponse(bulk_ticket_pdf_bytes(tickets), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{event.pk}-tickets.pdf"'
    return response
