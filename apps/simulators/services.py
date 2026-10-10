"""Local, trusted SimU capture ingestion and deliberate reconciliation."""

import hashlib
import json
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.audit import record_audit
from apps.events.models import EventRegistration
from apps.participants.normalization import normalize_phone
from apps.stations.models import EventStation, ExperienceSession, Station
from apps.stations.services import complete_simulator_from_capture
from .models import EventSimulatorConfiguration, SimulatorCapture


class CaptureConflict(Exception):
    """A submission ID was reused for different capture data."""


_SCORE_PATTERN = re.compile(r"^\d{1,3}(?:\.\d{1,6})?%?$")
_PHONE_FORMAT_PATTERN = re.compile(r"^\+?[0-9\s().-]+$")


def parse_total_score(raw):
    if not isinstance(raw, str) or not _SCORE_PATTERN.fullmatch(raw.strip()):
        raise ValidationError("TOTAL SCORE must be a percentage between 0 and 100.")
    value = raw.strip().removesuffix("%")
    try:
        score = Decimal(value)
    except InvalidOperation as exc:
        raise ValidationError("TOTAL SCORE is not numeric.") from exc
    if not Decimal("0") <= score <= Decimal("100"):
        raise ValidationError("TOTAL SCORE must be between 0 and 100.")
    return score


def normalize_identifier(raw, prefixes):
    value = (raw or "").strip()
    for prefix in sorted(prefixes, key=len, reverse=True):
        if value.casefold().startswith(prefix.casefold()):
            value = value[len(prefix):].strip()
            break
    if _PHONE_FORMAT_PATTERN.fullmatch(value):
        return normalize_phone(value)
    return value.casefold()


def _fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_snapshot(profile):
    return {
        "simulator_type": profile.simulator_type,
        "expected_width": profile.expected_width,
        "expected_height": profile.expected_height,
        "identifier_region": profile.identifier_region,
        "score_region": profile.score_region,
        "state_regions": profile.state_regions,
        "known_identifier_prefixes": profile.known_identifier_prefixes,
        "profile_version": profile.profile_version,
    }


def _audit_capture(capture, action, *, actor=None):
    record_audit(
        actor=actor, action=action, instance=capture, source="simulator_capture",
        new_data={"event_id": str(capture.event_id), "station_id": str(capture.station_id),
                  "status": capture.status, "review_reason": capture.review_reason},
    )


def _ingest_atomic(*, submission_id, event_id, station_code, profile_id, profile_version,
                   raw_identifier, raw_total_score, captured_at, diagnostic_metadata, fingerprint):
    with transaction.atomic():
        existing = SimulatorCapture.objects.select_for_update().filter(submission_id=submission_id).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise CaptureConflict("submission_id already belongs to another capture")
            return existing, True

        configuration = EventSimulatorConfiguration.objects.select_related(
            "event", "station", "profile"
        ).filter(event_id=event_id, station__code=station_code, profile_id=profile_id, is_active=True).first()
        if configuration is None or not configuration.station.is_active or not configuration.profile.is_active:
            raise ValidationError("No active simulator configuration matches this Event, Station, and profile.")
        if configuration.station.station_type != Station.Type.SIMULATOR:
            raise ValidationError("The configured Station is not a Simulator.")
        assignment = EventStation.objects.filter(event_id=event_id, station=configuration.station, enabled=True).first()
        if assignment is None:
            raise ValidationError("This Simulator Station is not assigned to the Event.")

        profile = configuration.profile
        normalized = normalize_identifier(raw_identifier, profile.known_identifier_prefixes)
        try:
            parsed_score = parse_total_score(raw_total_score)
        except ValidationError:
            parsed_score = None
        capture = SimulatorCapture.objects.create(
            submission_id=submission_id, request_fingerprint=fingerprint,
            event=configuration.event, station=configuration.station, profile=profile,
            profile_version=profile_version, profile_snapshot=_profile_snapshot(profile),
            raw_identifier=raw_identifier, normalized_identifier=normalized,
            raw_total_score=raw_total_score, total_score=parsed_score,
            captured_at=captured_at, diagnostic_metadata=diagnostic_metadata,
        )

        if normalized == settings.SIMULATOR_TEST_IDENTIFIER:
            capture.status = SimulatorCapture.Status.TEST
            capture.mode = SimulatorCapture.Mode.STAFF_TEST
            capture.review_reason = "invalid_score" if parsed_score is None else ""
            # An active permanent test run may receive this nonofficial capture.
            # No registration, official result, or official progression is touched.
            from apps.participants.models import TestParticipantRun
            from apps.stations.models import ExperienceActivity
            from apps.stations.services import activity_order_for_session, complete_activity
            run = TestParticipantRun.objects.select_for_update().select_related(
                "participant", "experience_session"
            ).filter(event=configuration.event, reset_at__isnull=True).first()
            update_fields = ["status", "mode", "review_reason"]
            if run is not None:
                session = run.experience_session
                if session.mode == "staff_test" and not session.registration_id and not session.participant_id:
                    capture.participant = run.participant
                    capture.experience_session = session
                    update_fields += ["participant", "experience_session"]
            capture.save(update_fields=update_fields)
            if run is not None and capture.experience_session_id and parsed_score is not None:
                session = run.experience_session
                order = activity_order_for_session(session)
                prior = order[:order.index(ExperienceActivity.Activity.SIMULATOR)]
                rows = {row.activity: row for row in session.activities.select_for_update()}
                ready = all(rows[key].status in (ExperienceActivity.Status.COMPLETED,
                    ExperienceActivity.Status.SKIPPED) for key in prior)
                simulator = rows.get(ExperienceActivity.Activity.SIMULATOR)
                if ready and simulator and simulator.status in (ExperienceActivity.Status.PENDING,
                    ExperienceActivity.Status.IN_PROGRESS) and session.completed_at is None:
                    if simulator.status == ExperienceActivity.Status.PENDING:
                        simulator.status = ExperienceActivity.Status.IN_PROGRESS
                        simulator.started_at = timezone.now()
                        simulator.save(update_fields=("status", "started_at"))
                    complete_activity(session=session, activity_name=ExperienceActivity.Activity.SIMULATOR, actor=None)
            _audit_capture(capture, "simulator.capture.test")
            return capture, False

        reason = ""
        event = configuration.event
        if parsed_score is None:
            reason = "invalid_score"
        elif not normalized:
            reason = "invalid_identifier"
        elif profile_version != profile.profile_version:
            reason = "profile_version_mismatch"
        elif not assignment.is_active_context:
            reason = "station_context_inactive"
        elif not event.is_current:
            reason = "event_not_open"
        else:
            candidates = list(EventRegistration.objects.select_related("participant").filter(
                event=event, status=EventRegistration.Status.ACTIVE,
                participant__contact_phone=normalized, participant__archived_at__isnull=True, participant__kind="person",
            )[:2])
            # Accept current tickets via the kiosk's event-scoped resolver.
            # Conflicting phone and ticket identities remain ambiguous.
            from apps.events.services import resolve_ticket
            ticket_resolution = resolve_ticket(normalized, expected_event=event)
            ticket_statuses = {"valid", "already_checked_in"}
            if ticket_resolution.status in ticket_statuses:
                ticket_registration = ticket_resolution.ticket.registration
                if ticket_registration.participant.archived_at is None:
                    candidates = list({registration.pk: registration for registration in
                                       [*candidates, ticket_registration]}.values())
            # A known ticket for a different or ineligible Event must not
            # fall back to an unrelated participant with the same phone digits.
            if ticket_resolution.ticket is not None and ticket_resolution.status not in ticket_statuses:
                reason = "unmatched_identifier"
            elif len(candidates) == 0:
                reason = "unmatched_identifier"
            elif len(candidates) > 1:
                reason = "ambiguous_identifier"
            else:
                registration = candidates[0]
                capture.registration = registration
                capture.participant = registration.participant
                experience = ExperienceSession.objects.select_for_update().filter(
                    registration=registration, event=event, participant=registration.participant,
                    mode=ExperienceSession.Mode.OFFICIAL,
                ).first()
                if experience is None:
                    reason = "experience_missing"
                else:
                    capture.experience_session = experience
                    if SimulatorCapture.objects.filter(
                        experience_session=experience, is_official=True, voided_at__isnull=True,
                    ).exists():
                        reason = "existing_official_result"
                    else:
                        try:
                            with transaction.atomic():
                                capture.status = SimulatorCapture.Status.MATCHED
                                capture.mode = SimulatorCapture.Mode.OFFICIAL
                                capture.is_official = True
                                capture.save(update_fields=("registration", "participant", "experience_session", "status", "mode", "is_official"))
                                complete_simulator_from_capture(session=experience)
                        except (ValidationError, IntegrityError):
                            capture.refresh_from_db()
                            reason = "experience_not_ready"
                        else:
                            _audit_capture(capture, "simulator.capture.matched")
                            return capture, False

        capture.status = SimulatorCapture.Status.NEEDS_REVIEW
        capture.mode = SimulatorCapture.Mode.UNRESOLVED
        capture.is_official = False
        capture.review_reason = reason
        capture.save(update_fields=("registration", "participant", "experience_session", "status", "mode", "is_official", "review_reason"))
        _audit_capture(capture, "simulator.capture.needs_review")
        return capture, False


def ingest_capture(*, submission_id, event_id, station_code, profile_id, profile_version,
                   raw_identifier, raw_total_score, captured_at, diagnostic_metadata=None):
    """Persist every configured capture; return (capture, idempotent_replay)."""
    try:
        submission_id = uuid.UUID(str(submission_id))
        event_id = uuid.UUID(str(event_id))
        profile_id = uuid.UUID(str(profile_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Use valid submission, Event, and profile UUIDs.") from exc
    for value, name, limit in (
        (station_code, "station_code", 64), (profile_version, "profile_version", 40),
        (raw_identifier, "raw_identifier", 128), (raw_total_score, "raw_total_score", 64),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValidationError(f"{name} must be a nonempty string of at most {limit} characters.")
    if not hasattr(captured_at, "tzinfo") or timezone.is_naive(captured_at):
        raise ValidationError("captured_at must contain a timezone offset.")
    if diagnostic_metadata is None:
        diagnostic_metadata = {}
    if not isinstance(diagnostic_metadata, dict):
        raise ValidationError("diagnostic_metadata must be an object.")
    payload = {
        "submission_id": str(submission_id), "event_id": str(event_id),
        "station_code": station_code, "profile_id": str(profile_id),
        "profile_version": profile_version, "raw_identifier": raw_identifier,
        "raw_total_score": raw_total_score, "captured_at": captured_at.isoformat(),
        "diagnostic_metadata": diagnostic_metadata,
    }
    try:
        fingerprint = _fingerprint(payload)
    except (TypeError, ValueError) as exc:
        raise ValidationError("diagnostic_metadata must be JSON serializable.") from exc
    try:
        return _ingest_atomic(
            submission_id=submission_id, event_id=event_id, station_code=station_code,
            profile_id=profile_id, profile_version=profile_version,
            raw_identifier=raw_identifier, raw_total_score=raw_total_score,
            captured_at=captured_at, diagnostic_metadata=diagnostic_metadata,
            fingerprint=fingerprint,
        )
    except IntegrityError:
        # Concurrent retry: the unique submission_id constraint is authoritative.
        existing = SimulatorCapture.objects.filter(submission_id=submission_id).first()
        if existing is None:
            raise
        if existing.request_fingerprint != fingerprint:
            raise CaptureConflict("submission_id already belongs to another capture")
        return existing, True


def reconcile_capture(*, capture, registration, actor, reason, corrected_total_score=None, replace_existing=False):
    """Authorized backend operation; no participant-facing reconciliation endpoint."""
    if not (getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False)):
        raise PermissionDenied("Staff authorization is required.")
    if not reason or not reason.strip():
        raise ValidationError("A reconciliation reason is required.")
    with transaction.atomic():
        capture = SimulatorCapture.objects.select_for_update().get(pk=capture.pk)
        if capture.status != SimulatorCapture.Status.NEEDS_REVIEW or capture.voided_at:
            raise ValidationError("Only an unresolved capture can be reconciled.")
        registration = EventRegistration.objects.select_related("participant").get(pk=registration.pk)
        if registration.event_id != capture.event_id or registration.status != EventRegistration.Status.ACTIVE:
            raise ValidationError("Choose an active registration from the capture Event.")
        experience = ExperienceSession.objects.select_for_update().filter(
            registration=registration, event_id=capture.event_id, participant=registration.participant,
            mode=ExperienceSession.Mode.OFFICIAL,
        ).first()
        if experience is None:
            raise ValidationError("The registration needs an official experience session.")
        score = parse_total_score(corrected_total_score) if corrected_total_score is not None else capture.total_score
        if score is None:
            raise ValidationError("A valid TOTAL SCORE is required before reconciliation.")
        previous = SimulatorCapture.objects.select_for_update().filter(
            experience_session=experience, is_official=True, voided_at__isnull=True,
        ).first()
        if previous and not replace_existing:
            raise ValidationError("An official result exists; explicit replacement is required.")
        if previous:
            previous.voided_at = timezone.now()
            previous.voided_by = actor
            previous.void_reason = reason.strip()[:500]
            previous.save(update_fields=("voided_at", "voided_by", "void_reason"))
        capture.registration = registration
        capture.participant = registration.participant
        capture.experience_session = experience
        capture.total_score = score
        capture.status = SimulatorCapture.Status.RESOLVED
        capture.mode = SimulatorCapture.Mode.OFFICIAL
        capture.is_official = True
        capture.review_reason = ""
        capture.resolved_at = timezone.now()
        capture.resolved_by = actor
        capture.resolution_reason = reason.strip()[:500]
        capture.save(update_fields=("registration", "participant", "experience_session", "total_score", "status", "mode", "is_official", "review_reason", "resolved_at", "resolved_by", "resolution_reason"))
        if previous:
            previous.replaced_by = capture
            previous.save(update_fields=("replaced_by",))
        else:
            complete_simulator_from_capture(session=experience)
        _audit_capture(capture, "simulator.capture.resolved", actor=actor)
        return capture
