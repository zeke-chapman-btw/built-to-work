"""Canonical eligibility predicates for future reporting and sharing.

A caller still needs its own authorization and Event/customer scope. These querysets
only establish official versus nonofficial data eligibility.
"""
from .models import Participant


def official_participants():
    return Participant.objects.filter(kind=Participant.Kind.PERSON, archived_at__isnull=True)


def official_registrations():
    from apps.events.models import EventRegistration
    return EventRegistration.objects.filter(
        status=EventRegistration.Status.ACTIVE, participant__kind=Participant.Kind.PERSON,
        participant__archived_at__isnull=True,
    )


def official_experiences():
    from apps.stations.models import ExperienceSession
    return ExperienceSession.objects.filter(
        mode=ExperienceSession.Mode.OFFICIAL, registration__isnull=False,
        registration__status="active", participant__kind=Participant.Kind.PERSON,
        participant__archived_at__isnull=True,
    )


def official_quiz_attempts():
    from apps.assessments.models import QuizAttempt
    return QuizAttempt.objects.filter(
        is_official=True, voided_at__isnull=True,
        experience_session__mode="official", experience_session__registration__status="active",
        participant__kind=Participant.Kind.PERSON, participant__archived_at__isnull=True,
    )


def official_game_sessions():
    from apps.games.models import GameSession
    return GameSession.objects.filter(
        mode=GameSession.Mode.OFFICIAL, status=GameSession.Status.COMPLETED,
        voided_at__isnull=True, experience_session__mode="official",
        experience_session__participant__kind=Participant.Kind.PERSON,
        experience_session__participant__archived_at__isnull=True,
        experience_session__registration__status="active",
    )


def official_simulator_captures():
    from apps.simulators.models import SimulatorCapture
    return SimulatorCapture.objects.filter(
        mode=SimulatorCapture.Mode.OFFICIAL, is_official=True,
        status=SimulatorCapture.Status.MATCHED, voided_at__isnull=True,
        participant__kind=Participant.Kind.PERSON, participant__archived_at__isnull=True,
        registration__status="active", experience_session__mode="official",
    )
