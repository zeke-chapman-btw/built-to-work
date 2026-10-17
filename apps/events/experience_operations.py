"""Read-only event experience overview, using existing authoritative records."""
from django.conf import settings
from django.db.models import Avg, Count, F, Max, Q
from apps.assessments.models import QuizAttempt
from apps.assessments.services import eligible_event_categories
from apps.games.models import GameSession
from apps.games.services import configured_game
from apps.simulators.models import EventSimulatorConfiguration, SimulatorCapture
from apps.stations.models import ExperienceActivity, ExperienceSession
from .models import CentralEventDownload, EventRegistration, OfflineEventPreparation, SyncOutboxItem
from .offline import check_offline_readiness

OPERATIONS_PERMISSIONS = (
    "events.view_event", "events.view_eventregistration", "participants.view_participant",
    "stations.view_experiencesession", "assessments.view_quizattempt",
    "games.view_gamesession", "simulators.view_simulatorcapture",
)

def can_view_event_operations(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perms(OPERATIONS_PERMISSIONS))

def _score_summary(queryset, field):
    return queryset.aggregate(average=Avg(field), high=Max(field), records=Count(field))

def event_experience_overview(event, *, include_offline=False):
    """No attendance, network probes, writes, or simulated completion."""
    registrations = EventRegistration.objects.filter(event=event, status="active", participant__kind="person")
    all_sessions = ExperienceSession.objects.filter(event=event, mode="official")
    sessions = all_sessions.filter(registration__event=event, registration__status="active",
        registration__participant_id=F("participant_id"), participant__kind="person")
    completed = sessions.filter(completed_at__isnull=False, activities__isnull=False).distinct().exclude(
        activities__status__in=("pending", "in_progress"))
    quizzes = QuizAttempt.objects.filter(experience_session__in=sessions, is_official=True,
        voided_at__isnull=True, participant_id=F("experience_session__participant_id")).exclude(status="void")
    finished_quizzes = quizzes.filter(status="complete")
    games = GameSession.objects.filter(event=event, experience_session__in=sessions,
        mode="official", replaced_by__isnull=True).exclude(status="voided")
    captures = SimulatorCapture.objects.filter(event=event).exclude(status="test").exclude(mode__in=("staff_test", "demo"))
    accepted_captures = captures.filter(experience_session__in=sessions,
        registration_id=F("experience_session__registration_id"), participant_id=F("experience_session__participant_id"),
        is_official=True, voided_at__isnull=True, status__in=("matched", "resolved"))
    metrics = [
        ("Registered participants", registrations.values("participant_id").distinct().count(), "Distinct participants with active registrations."),
        ("Official experience sessions", sessions.count(), "One per registration; test/demo sessions excluded."),
        ("Kiosk participants started", quizzes.values("participant_id").distinct().count(), "Actual accepted assessment attempts, not QR scans."),
        ("Kiosk participants completed", finished_quizzes.values("participant_id").distinct().count(), "Completed, nonvoid official assessments."),
        ("Kiosk attempts", quizzes.count(), "Accepted attempts; superseded/voided assessments excluded."),
        ("Game participants completed", games.filter(status="completed").values("experience_session__participant_id").distinct().count(), "Accepted Game result records."),
        ("Simulator capture submissions", captures.count(), "Includes unresolved/repeated capture attempts with distinct submission IDs."),
        ("Simulator participants completed", accepted_captures.values("participant_id").distinct().count(), "Distinct participants with a current accepted official result."),
        ("Completed experience sessions", completed.count(), "Recorded completion with no pending/in-progress activity."),
        ("Participants with completed experiences", completed.values("participant_id").distinct().count(), "Distinct participants, separate from session counts."),
        ("Incomplete experience sessions", sessions.count() - completed.count(), "Recorded sessions still unfinished; interruption is not inferred."),
        ("Completed sessions with staff exceptions", completed.filter(activities__status="skipped").distinct().count(), "Audited staff skips; not proof every step was performed."),
    ]
    quiz_scores = finished_quizzes.filter(sections__isnull=False).distinct().annotate(
        correct=Count("sections__responses", filter=Q(sections__responses__is_correct=True)))
    scores = [
        {"label": "Kiosk", "unit": "correct answers per accepted completed attempt", **_score_summary(quiz_scores, "correct")},
        {"label": "Game", "unit": "raw game points", **_score_summary(games.filter(status="completed"), "raw_score")},
        {"label": "Simulator", "unit": "TOTAL SCORE (%)", **_score_summary(accepted_captures, "total_score")},
    ]
    categories = eligible_event_categories(event)
    game = configured_game(event)
    simulator_configs = list(EventSimulatorConfiguration.objects.filter(event=event, is_active=True,
        station__is_active=True, profile__is_active=True).select_related("station", "profile"))
    active_types = set(event.station_assignments.filter(enabled=True, is_active_context=True,
        station__is_active=True).values_list("station__station_type", flat=True))
    checks = [
        {"label": "Kiosk assessment configuration", "state": "Configured" if len(categories) >= 2 else "Needs configuration", "detail": f"{len(categories)} eligible categories; at least two are required."},
        {"label": "Active Kiosk station context", "state": "Configured" if "kiosk" in active_types else "Not active", "detail": "A station must be enabled, active, and explicitly assigned this event context."},
        {"label": "Game integration", "state": "Duck Hunt adapter configured" if game and game.game.implementation_key == "duck_hunt" else "Integration gap", "detail": (game.game.display_name + "; external adapters are not certified by configuration alone.") if game else "No Game configured. Existing no-game experiences use Kiosk → Simulator; the intended Duck sequence is not available until configured."},
        {"label": "Simulator configuration", "state": "Configured" if simulator_configs else "Not configured", "detail": f"{len(simulator_configs)} active event/station/profile configurations. This is not a hardware or calibration test."},
        {"label": "Active Simulator station context", "state": "Configured" if "simulator" in active_types else "Not active", "detail": "Trusted capture still validates event, station, profile, identity and progression."},
        {"label": "Simulator capture endpoint", "state": "Credential configured" if bool(settings.SIMULATOR_INGESTION_TOKEN) else "Not configured", "detail": "Existing authenticated endpoint only. No external simulator connection is asserted."},
    ]
    exceptions = list(captures.filter(status="needs_review", voided_at__isnull=True).values("review_reason").annotate(total=Count("pk")).order_by("review_reason"))
    excluded = all_sessions.count() - sessions.count()
    inconsistent = sessions.filter(completed_at__isnull=False).exclude(pk__in=completed.values("pk")).count()
    offline = None
    if include_offline:
        preparations = []
        for prep in OfflineEventPreparation.objects.filter(event=event).select_related("trailer", "consent_document"):
            readiness = check_offline_readiness(prep)
            preparations.append({"id": prep.pk, "trailer": prep.trailer.identity, "status": prep.get_status_display(),
                "ready": readiness.ready, "failures": readiness.failures, "sync_identity": prep.sync_identity,
                "last_check": prep.last_readiness_check_at})
        outbox = list(SyncOutboxItem.objects.filter(preparation__event=event).values("status").annotate(total=Count("pk")).order_by("status"))
        downloads = list(CentralEventDownload.objects.filter(event_id=event.pk).values("status", "last_success_at"))
        latest = SyncOutboxItem.objects.filter(preparation__event=event, accepted_at__isnull=False).aggregate(value=Max("accepted_at"))["value"]
        offline = {"preparations": preparations, "outbox": outbox, "downloads": downloads, "last_accepted_at": latest}
    return {"metrics": [{"label": label, "value": value, "detail": detail} for label, value, detail in metrics],
        "scores": scores, "checks": checks, "exceptions": exceptions,
        "excluded_sessions": excluded, "inconsistent_sessions": inconsistent, "offline": offline,
        "sessions": sessions.select_related("participant", "registration").prefetch_related("activities").order_by("-started_at")[:20]}
