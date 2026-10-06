"""Browser-bound Duck Hunt adapter for the generic GameSession framework."""
import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from apps.assessments.views import _kiosk_session
from . import duck_hunt
from .models import GameSession
from .services import reset_nonofficial_session


def _run(request, station_code, session_id, game_session_id):
    station, experience = _kiosk_session(request, station_code, session_id)
    run = get_object_or_404(GameSession.objects.select_related("game", "event", "experience_session"),
        pk=game_session_id, experience_session=experience, event=experience.event, mode=experience.mode)
    return station, experience, run


def render_game(request, station, experience, run):
    try:
        run = duck_hunt.prepare(run)
    except ValidationError:
        return render(request, "games/duck_hunt.html", {"station": station, "experience": experience,
            "test_mode": experience.mode != "official", "state": "unavailable"}, status=409)
    kwargs = {"station_code": station.code, "session_id": experience.pk, "game_session_id": run.pk}
    state = "results" if run.status == GameSession.Status.COMPLETED else "ready" if run.status == GameSession.Status.NOT_STARTED else "interrupted"
    context = {"station": station, "experience": experience, "game_session": run,
        "test_mode": run.mode != GameSession.Mode.OFFICIAL, "state": state,
        "result": run.result_data.get("duck_hunt", {}),
        "game_data": {"state": state, "startUrl": reverse("games:duck_start", kwargs=kwargs),
            "finishUrl": reverse("games:duck_finish", kwargs=kwargs), "snapshot": run.configuration_snapshot["duck_hunt"]}}
    response = render(request, "games/duck_hunt.html", context)
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@require_POST
def start(request, station_code, session_id, game_session_id):
    _, _, run = _run(request, station_code, session_id, game_session_id)
    try:
        run = duck_hunt.activate(run)
    except ValidationError:
        return JsonResponse({"message": "This round cannot start. Please ask a team member for help."}, status=409)
    return JsonResponse({"starts_in_ms": max(0, (run.started_at - timezone.now()).total_seconds() * 1000)})


@never_cache
@require_POST
def finish(request, station_code, session_id, game_session_id):
    _, _, run = _run(request, station_code, session_id, game_session_id)
    if len(request.body) > 300000:
        return JsonResponse({"message": "The round could not be saved. Please ask a team member for help."}, status=400)
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict) or set(body) != {"shots"}:
            raise ValueError
        run = duck_hunt.accept_result(run, body["shots"])
    except (ValueError, TypeError, ValidationError):
        return JsonResponse({"message": "The round could not be saved yet. Please retry or ask a team member for help."}, status=409)
    return JsonResponse({"result": {key: run.result_data["duck_hunt"][key] for key in ("score", "kills", "accuracy")}})


@require_POST
def replay(request, station_code, session_id, game_session_id):
    _, _, run = _run(request, station_code, session_id, game_session_id)
    if run.mode == GameSession.Mode.OFFICIAL:
        raise PermissionDenied
    reset_nonofficial_session(game_session=run)
    return redirect("games:launch", station_code=station_code, session_id=session_id)
