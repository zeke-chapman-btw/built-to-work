"""Screen library: every participant-facing layout, drawn from its real template with sample data.

Nothing here reads or writes participant records, tickets or sessions. Each screen is rendered with
made-up objects so staff can review layouts that otherwise only appear on the trailer or during sign-up.
"""
import uuid
from base64 import b64encode
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace as NS

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

KIOSK = ("Kiosk", 1920, 1080)
DISPLAY = ("Trailer display", 1920, 1080)
PHONE = ("Phone", 390, 844)

NOT_IN_USE = "Not in use"

SAMPLE_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _qr(text):
    import qrcode
    from qrcode.image.svg import SvgPathImage
    out = BytesIO()
    qrcode.make(text, image_factory=SvgPathImage).save(out)
    return "data:image/svg+xml;base64," + b64encode(out.getvalue()).decode("ascii")


def _qr_png(text):
    import qrcode
    out = BytesIO()
    qrcode.make(text).save(out, format="PNG")
    return b64encode(out.getvalue()).decode("ascii")


def _event():
    start = timezone.localtime().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=14)
    return NS(pk=SAMPLE_ID, id=SAMPLE_ID, name="Sample Career Day", start_at=start, end_at=start + timedelta(days=1, hours=8),
              local_start_at=start, timezone_name="America/New_York", location_name="Sample Training Center",
              city="Augusta", state="GA", group_mode="disabled", group_label="Group", description="")


def _ticket(event=None):
    event = event or _event()
    participant = NS(first_name="Taylor", preferred_name="", last_name="Sample")
    registration = NS(participant=participant, event=event, event_id=event.pk, group_id=None, group=None)
    return NS(pk=SAMPLE_ID, token=SAMPLE_ID, ticket_number="0000000123", registration=registration)


def _station():
    return NS(code="preview", name="Sample Station")


def _config(n, name):
    return NS(id=n, display_name=name, category=NS(name=name))


def _kiosk_base():
    return {"preview_mode": True, "station_code": "preview", "station": _station()}


def _question():
    choices = [NS(key=k, text=t) for k, t in (("a", "Hammer"), ("b", "Wrench"), ("c", "Level"), ("d", "Tape measure"))]
    return {**_kiosk_base(), "section": NS(category_name_snapshot="Tool Knowledge", position=1),
            "question": NS(text="Which tool checks that a surface is flat?", position=4, image_url=""),
            "choices": choices, "remaining_seconds": 42, "countdown_active": False}


def _results():
    sections = [NS(category_name_snapshot="Tool Knowledge", correct_count=11), NS(category_name_snapshot="Safety", correct_count=12)]
    return {**_kiosk_base(), "attempt": NS(total_correct=23, sections=NS(all=sections)),
            "session": NS(id=SAMPLE_ID), "game_configuration": NS(game=NS(display_name="Duck Hunt"))}


def _leaderboard():
    rows = [NS(rank=i + 1, alias=a, score=s) for i, (a, s) in enumerate((("Falcon", 94), ("Badger", 91), ("Otter", 88), ("Hawk", 84), ("Maple", 80)))]
    return {"preview_mode": True, "binding": NS(pk=SAMPLE_ID, preparation=NS(event=NS(name="Sample Career Day"), event_id=SAMPLE_ID)),
            "board": NS(groups=[NS(profile="Standard", simulator="Excavator", version="1", rows=rows)])}


def _duck(state):
    from apps.games import duck_hunt
    from copy import deepcopy
    seed = 12345
    snap = {"seed": seed, "rules": deepcopy(duck_hunt.RULES), "targets": duck_hunt.opportunity_schedule(seed)}
    return {**_kiosk_base(), "experience": NS(pk=SAMPLE_ID), "game_session": NS(pk=SAMPLE_ID), "test_mode": False, "state": state,
            "first_name": "Taylor", "can_restart": state == "interrupted",
            "result": {"score": 770, "kills": 35, "accuracy": 95},
            "game_data": {"state": "preview", "startUrl": "#", "finishUrl": "#", "snapshot": snap}}


def _intake_finish(pending=False):
    ticket = _ticket()
    ctx = {"event": ticket.registration.event}
    if pending:
        ctx["ticket_pending"] = True
    else:
        ctx.update(ticket=ticket, ticket_qr_data=_qr("tel:" + ticket.ticket_number), ticket_image_url=_image_url())
    return ctx


def _ticket_page():
    ticket = _ticket()
    return {"ticket": ticket, "event": ticket.registration.event, "ticket_qr_data": _qr("tel:" + ticket.ticket_number),
            "ticket_image_url": _image_url()}


def _image_url():
    from django.urls import reverse
    return reverse("operations:screen-image", args=["ticket-image-phone"])


# (key, group, title, device, template, context builder)
SCREENS = [
    ("kiosk-home", "Kiosk", "Home: scan your ticket", KIOSK, "stations/kiosk_home.html", lambda: _kiosk_base()),
    ("kiosk-signup", "Kiosk", "Sign up here", KIOSK, "stations/kiosk_signup.html",
     lambda: {**_kiosk_base(), "signup_qr_data": _qr_png("https://example.org/sign-up")}),
    ("kiosk-not-ready", "Kiosk", "Station not ready", KIOSK, "assessments/not_ready.html", lambda: _kiosk_base()),
    ("quiz-instructions", "Quiz", "Instructions", KIOSK, "assessments/instructions.html", lambda: _kiosk_base()),
    ("quiz-categories", "Quiz", "Choose categories", KIOSK, "assessments/category_selection.html",
     lambda: {**_kiosk_base(), "configs": [_config(1, "Tool Knowledge"), _config(2, "Safety"), _config(3, "Math"), _config(4, "Reading Plans")]}),
    ("quiz-categories-many", "Quiz", "Choose categories (7+ options)", KIOSK, "assessments/category_selection.html",
     lambda: {**_kiosk_base(), "configs": [_config(i, n) for i, n in enumerate(("Auto Mechanic", "Civil Construction", "Electrical", "Plumbing", "Welding", "Construction Labor", "HVAC", "Warehouse"), 1)]}),
    ("quiz-question", "Quiz", "Question", KIOSK, "assessments/question.html", _question),
    ("quiz-transition", "Quiz", "Between categories", KIOSK, "assessments/transition.html",
     lambda: {**_kiosk_base(), "attempt": NS(experience_session_id=SAMPLE_ID), "next_category": "Safety"}),
    ("quiz-results", "Quiz", "Results", KIOSK, "assessments/results.html", _results),
    ("quiz-handoff", "Quiz", "Next up: Duck Hunt", KIOSK, "assessments/duck_handoff.html",
     lambda: {**_kiosk_base(), "first_name": "Taylor", "session": NS(id=SAMPLE_ID)}),
    ("game-placeholder", "Games", "Game start", KIOSK, "games/placeholder.html",
     lambda: {**_kiosk_base(), "game": NS(display_name="Duck Hunt"), "game_session": NS(id=SAMPLE_ID), "test_mode": False}),
    ("duck-ready", "Games", "Duck Hunt: shoot to start", KIOSK, "games/duck_hunt.html", lambda: _duck("ready")),
    ("duck-results", "Games", "Duck Hunt: results", KIOSK, "games/duck_hunt.html", lambda: _duck("results")),
    ("duck-problem", "Games", "Duck Hunt: something went wrong", KIOSK, "games/duck_hunt.html", lambda: _duck("interrupted")),
    ("game-simulator-next", "Games", "Go to the simulator", KIOSK, "games/simulator_next.html", lambda: {**_kiosk_base(), "test_mode": False}),
    ("display-leaderboard", "Trailer displays", "Simulator leaderboard", DISPLAY, "trailer_control/leaderboard.html", _leaderboard),
    ("display-video", "Trailer displays", "Video display (no video loaded)", DISPLAY, "trailer_control/video.html",
     lambda: {"binding": NS(pk=SAMPLE_ID, preparation=NS(event_id=SAMPLE_ID)), "has_media": False}),
    ("signup-start", "Sign-up", "Registration start", PHONE, "participants/registration_start.html", lambda: {"event": _event()}),
    ("signup-unavailable", "Sign-up", "Registration unavailable", PHONE, "participants/registration_unavailable.html",
     lambda: {"reason": "Sign-up for this event is closed."}),
    ("intake-start", "Sign-up", "Intake welcome", PHONE, "participants/intake_start.html", lambda: {"event": _event(), "assignment": NS(pk=SAMPLE_ID)}),
    ("intake-finish", "Sign-up", "Finished: here's your ticket", PHONE, "participants/intake_complete.html", _intake_finish),
    ("intake-finish-pending", "Sign-up", "Finished: ticket on its way", PHONE, "participants/intake_complete.html", lambda: _intake_finish(True)),
    ("ticket-page", "Ticket", "Ticket page (link from text/email)", PHONE, "events/ticket_present.html", _ticket_page),
    ("ticket-unavailable", "Ticket", "Ticket can't be used", PHONE, "events/ticket_unavailable.html",
     lambda: {"resolution": NS(status="expired")}),
    # Not in use: kept for reference, not reached in the live flow.
    ("quiz-ready", NOT_IN_USE, "Quiz: ready to start (the quiz now starts right after picking categories)", KIOSK, "assessments/ready.html",
     lambda: {**_kiosk_base(), "chosen": [_config(1, "Tool Knowledge"), _config(2, "Safety")]}),
]
IMAGES = [
    ("ticket-image-phone", "Ticket", "Saved ticket image (phone)", PHONE),
    ("ticket-image-print", "Ticket", "Printable ticket", ("Print", 1100, 700)),
]
INDEX = {s[0]: s for s in SCREENS}


def _allowed(user):
    if not (user.is_authenticated and user.is_active and user.is_staff):
        raise PermissionDenied
    if not (user.is_superuser or user.has_perm("stations.view_station") or user.has_perm("assessments.view_assessmentcategory")):
        raise PermissionDenied


@login_required(login_url="/admin/login/")
@require_GET
def index(request):
    _allowed(request.user)
    groups = {}
    for key, group, title, device, _template, _builder in SCREENS:
        groups.setdefault(group, []).append({"key": key, "title": title, "device": device[0], "width": device[1], "height": device[2], "image": False})
    for key, group, title, device in IMAGES:
        groups.setdefault(group, []).append({"key": key, "title": title, "device": device[0], "width": device[1], "height": device[2], "image": True})
    if NOT_IN_USE in groups:
        groups[NOT_IN_USE] = groups.pop(NOT_IN_USE)
    return render(request, "operations/screen_library.html", {"page_title": "Screens", "groups": groups.items(), "not_in_use": NOT_IN_USE})


@login_required(login_url="/admin/login/")
@require_GET
@xframe_options_sameorigin
def preview(request, key):
    _allowed(request.user)
    screen = INDEX.get(key)
    if screen is None:
        raise Http404
    _key, _group, _title, _device, template, builder = screen
    html = render_to_string(template, builder(), request=request)
    response = HttpResponse(html)
    response["Cache-Control"] = "private, no-store"
    return response


@login_required(login_url="/admin/login/")
@require_GET
@xframe_options_sameorigin
def image(request, key):
    _allowed(request.user)
    from apps.events import ticket_renderer
    ticket = _ticket()
    if key == "ticket-image-phone":
        data = ticket_renderer.ticket_phone_png_bytes(ticket)
    elif key == "ticket-image-print":
        data = ticket_renderer.ticket_png_bytes(ticket)
    else:
        raise Http404
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    return response
