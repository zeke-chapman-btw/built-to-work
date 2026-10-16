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

PAGE_SIZE = 25


def _staff_only(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
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
        "can_view_qualifications": _can(request.user, "participants.view_participantqualification", "participants.manage_participant_qualifications"),
        "can_view_documents": _can(request.user, "participants.view_participantdocument", "participants.manage_participant_documents"),
        "can_manage_documents": _can(request.user, "participants.manage_participant_documents"),
        "can_view_quiz_results": _can(request.user, "assessments.view_quizattempt"),
        "can_view_game_results": _can(request.user, "games.view_gamesession"),
        "can_view_historical_scores": _can(request.user, "participants.view_historicalsimulatorscore"),
    }


@login_required
@_staff_only
@permission_required("participants.view_participant", raise_exception=True)
def staff_directory(request):
    search_query = request.GET.get("q", "").strip()[:150]
    participants = Participant.objects.annotate(
        registration_count=Count("event_registrations", distinct=True),
        latest_registration=Max("event_registrations__registered_at"),
    )
    if search_query:
        terms = search_query.split()
        lookup = (
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(preferred_name__icontains=search_query)
            | Q(contact_email__icontains=search_query)
            | Q(contact_phone__icontains=search_query)
        )
        if len(terms) > 1:
            lookup |= (Q(first_name__icontains=terms[0]) & Q(last_name__icontains=" ".join(terms[1:])))
            lookup |= (Q(first_name__icontains=" ".join(terms[:-1])) & Q(last_name__icontains=terms[-1]))
        digits = "".join(character for character in search_query if character.isdigit())
        if digits:
            normalized_phone = F("contact_phone")
            for character in (" ", "-", "(", ")", ".", "+"):
                normalized_phone = Replace(normalized_phone, Value(character), Value(""))
            participants = participants.annotate(_phone_digits=normalized_phone)
            lookup |= Q(_phone_digits__contains=digits)
        participants = participants.filter(lookup)
    total_participants = participants.count()
    paginator = Paginator(participants.order_by("last_name", "first_name", "pk"), PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = _staff_workspace_context(
        request, title="Participants", description="Search participant records and open a unified profile."
    )
    context.update({
        "page_obj": page_obj,
        "search_query": search_query,
        "total_participants": total_participants,
        "has_search": bool(search_query),
    })
    return render(request, "participants/staff_directory.html", context)


@login_required
@_staff_only
@permission_required("participants.view_participant", raise_exception=True)
def staff_profile(request, participant_id):
    participant = get_object_or_404(Participant, pk=participant_id)
    context = _staff_workspace_context(
        request, title="Participant profile", description="Review this participant's existing records and history."
    )
    context["participant"] = participant
    context["display_name"] = participant.preferred_name or participant.first_name

    if context["can_view_registrations"]:
        registrations = list(
            EventRegistration.objects.filter(participant=participant)
            .select_related("event", "group")
            .order_by("-registered_at", "pk")
        )
        context["registrations"] = registrations
        context["registration_tickets"] = {}
        if context["can_view_tickets"] and registrations:
            ticket_qs = QrTicket.objects.filter(
                registration__in=registrations, is_current=True, revoked_at__isnull=True
            ).order_by("-issued_at", "pk")
            for ticket in ticket_qs:
                context["registration_tickets"].setdefault(ticket.registration_id, ticket)
    else:
        context["registrations"] = []
        context["registration_tickets"] = {}

    if context["can_view_qualifications"]:
        context["qualifications"] = ParticipantQualification.objects.filter(
            participant=participant, is_active=True
        ).select_related("catalog_entry", "catalog_entry__category").order_by("catalog_entry__category__position", "catalog_entry__position", "custom_name")
    else:
        context["qualifications"] = []

    if context["can_view_documents"]:
        context["documents"] = ParticipantDocument.objects.filter(participant=participant).select_related(
            "category"
        ).order_by("-uploaded_at", "pk")
    else:
        context["documents"] = []

    context["quiz_attempts"] = (
        QuizAttempt.objects.filter(participant=participant).select_related("experience_session", "experience_session__event").order_by("-completed_at", "-selected_at")
        if context["can_view_quiz_results"] else []
    )
    context["game_sessions"] = (
        GameSession.objects.filter(experience_session__participant=participant).select_related(
            "game", "experience_session", "experience_session__event"
        ).order_by("-completed_at", "-started_at")
        if context["can_view_game_results"] else []
    )
    context["historical_scores"] = (
        HistoricalSimulatorScore.objects.filter(participant=participant).order_by("-scored_at", "-pk")
        if context["can_view_historical_scores"] else []
    )
    return render(request, "participants/staff_profile.html", context)
