"""Accepted activity sources shared by event and participant reporting.

These querysets select stored evidence; they do not score, reconcile, or mutate it.
"""
from django.db.models import F
from apps.events.models import EventRegistration, Attendance
from apps.stations.models import ExperienceSession
from apps.assessments.models import QuizAttempt
from apps.simulators.models import SimulatorCapture
from apps.games.models import GameSession


def activity_sources(event=None):
    regs = EventRegistration.objects.filter(status="active", participant__kind="person")
    sessions = ExperienceSession.objects.filter(
        mode="official", registration__status="active", participant__kind="person",
        registration__participant_id=F("participant_id"), registration__event_id=F("event_id"))
    if event is not None:
        regs = regs.filter(event=event)
        sessions = sessions.filter(event=event)
    quizzes = QuizAttempt.objects.filter(experience_session__in=sessions, is_official=True,
        voided_at__isnull=True, participant_id=F("experience_session__participant_id")).exclude(status="void")
    finished = quizzes.filter(status="complete", completed_at__isnull=False)
    captures = SimulatorCapture.objects.filter(experience_session__in=sessions,
        event_id=F("experience_session__event_id"), registration_id=F("experience_session__registration_id"),
        participant_id=F("experience_session__participant_id"), is_official=True, mode="official",
        voided_at__isnull=True, status__in=("matched", "resolved"))
    games = GameSession.objects.filter(experience_session__in=sessions,
        event_id=F("experience_session__event_id"), mode="official", status="completed",
        completed_at__isnull=False, voided_at__isnull=True)
    attendance = Attendance.objects.filter(is_void=False, registration__status="active",
        registration__event_id=F("event_id"), registration__participant_id=F("participant_id"), participant__kind="person")
    if event is not None:
        attendance = attendance.filter(event=event)
    return dict(registrations=regs, sessions=sessions, quizzes=quizzes, finished_quizzes=finished,
                captures=captures, games=games, attendance=attendance)
