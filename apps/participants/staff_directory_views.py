from functools import wraps

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q, F, Value
from django.db.models.functions import Replace
from django.shortcuts import get_object_or_404, render

from apps.assessments.models import QuizAttempt
from apps.events.models import EventRegistration, QrTicket
from apps.games.models import GameSession
from .models import HistoricalSimulatorScore, Participant
from .participant_document_models import ParticipantDocument
from .qualification_models import ParticipantQualification
from .participant_review_views import can_view_participant_reviews
from .verification_services import can_view_qualification_approvals

PAGE_SIZE = 25


def _staff_only(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped


def _can(user, *permissions):
    return user.is_superuser or any(user.has_perm(permission) for permission in permissions)


def _staff_workspace_context(request, *, title, description):
    return {
        "page_title": title,
        "page_description": description,
        "can_view_registrations": _can(request.user, "events.view_eventregistration"),
        "can_view_tickets": _can(request.user, "events.view_qrticket"),
        "can_view_qualifications": _can(request.user, "participants.view_participantqualification"),
        "can_view_documents": _can(request.user, "participants.view_participantdocument"),
        "can_manage_documents": _can(request.user, "participants.manage_participant_documents"),
        "can_view_quiz_results": _can(request.user, "assessments.view_quizattempt"),
        "can_view_game_results": _can(request.user, "games.view_gamesession"),
        "can_view_historical_scores": _can(request.user, "participants.view_historicalsimulatorscore"),
    }



def staff_directory(request):
    from .workspace_views import directory
    return directory(request)


def staff_profile(request, participant_id):
    from .workspace_views import profile
    return profile(request, participant_id)
