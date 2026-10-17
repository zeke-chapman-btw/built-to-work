from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from apps.stations.services import activity_order_for_session
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.assessments.models import QuizAttempt
from apps.assessments.views import _current_attempt, _kiosk_session
from .models import GameSession
from .services import complete_test_session, configured_game, resolve_and_start

def launch(request, station_code, session_id):
    station, experience = _kiosk_session(request, station_code, session_id)
    attempt = _current_attempt(experience)
    if not attempt or attempt.status != QuizAttempt.Status.COMPLETE:
        return redirect('assessments:station_start', station_code=station.code, session_id=experience.pk)
    if "game" not in activity_order_for_session(experience):
        return redirect("games:simulator_next", station_code=station.code, session_id=experience.pk)
    config = configured_game(experience.event)
    if config is None:
        if experience.mode == "official":
            return HttpResponse("The configured Game step needs staff review.", status=409)
        return redirect("games:simulator_next", station_code=station.code, session_id=experience.pk)
    try:
        run = resolve_and_start(experience_session=experience)
    except ValidationError:
        return HttpResponse("This experience is not ready for its Game step. Please ask staff for help.", status=409)
    if config.game.implementation_key == 'duck_hunt':
        from .duck_views import render_game
        return render_game(request, station, experience, run)
    return render(request, 'games/placeholder.html', {'station': station, 'experience': experience, 'game_session': run, 'game': config.game, 'test_mode': run.mode != GameSession.Mode.OFFICIAL})

@require_POST
def complete_test(request, game_session_id):
    run = get_object_or_404(GameSession.objects.select_related('experience_session'), pk=game_session_id)
    if run.mode == GameSession.Mode.OFFICIAL or run.game.implementation_key == 'duck_hunt':
        raise PermissionDenied
    station, experience = _kiosk_session(request, request.POST.get('station_code', ''), run.experience_session_id)
    if experience.pk != run.experience_session_id:
        raise PermissionDenied
    complete_test_session(game_session=run)
    return render(request, 'games/test_complete.html', {'station': station, 'experience': experience, 'game_session': run})

def simulator_next(request, station_code, session_id):
    station, experience = _kiosk_session(request, station_code, session_id)
    attempt = _current_attempt(experience)
    if not attempt or attempt.status != QuizAttempt.Status.COMPLETE:
        return redirect('assessments:results', station_code=station.code, session_id=experience.pk)
    config = configured_game(experience.event)
    order = activity_order_for_session(experience)
    if "game" in order and config is not None:
        completed_game = GameSession.objects.filter(experience_session=experience, event=experience.event, game=config.game,
            mode=experience.mode, status=GameSession.Status.COMPLETED, replaced_by__isnull=True).exists()
        if not completed_game:
            return redirect("games:launch", station_code=station.code, session_id=experience.pk)
    elif "game" in order and experience.mode == "official":
        return HttpResponse("The configured Game step needs staff review.", status=409)
    if "duck" in order and experience.mode == "official" and not experience.activities.filter(activity="duck", status__in=("completed", "skipped")).exists():
        return HttpResponse("The legacy Duck integration needs staff review.", status=409)
    return render(request, "games/simulator_next.html", {"station": station, "experience": experience, "test_mode": experience.mode != "official"})
