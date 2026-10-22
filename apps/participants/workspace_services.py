"""Audited staff management over existing canonical participant records."""
import hashlib
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_audit
from .models import Participant, ParticipantCareerProfile
from .workspace_models import ParticipantAcquisition, ParticipantWorkExperience, ParticipantNote, SavedParticipantView
from .workspace_access import require_people, can_notes, can_edit_note
from .identity import update_participant_profile, participant_candidates, canonical_id

PERSON_FIELDS = ("first_name", "last_name", "preferred_name", "contact_email", "contact_phone", "city", "state", "postal_code")
CAREER_FIELDS = ("employment_status", "employment_interest", "career_interests", "skills_interests", "years_of_experience",
                 "education_training", "work_availability", "willing_to_travel", "preferred_locations", "preferred_work_type")


def audit(actor, action, instance, *, before=None, after=None, reason=""):
    return record_audit(actor=actor, action="participant.workspace." + action, instance=instance,
        source="participant_staff_workspace", old_data=before, new_data=after, reason=reason)


def snapshot(instance, fields):
    return {key: (str(value) if value is not None and not isinstance(value, (str, int, float, bool)) else value)
            for key in fields for value in [getattr(instance, key)]}


def require_reason(reason):
    if not (reason or "").strip():
        raise ValidationError("An attributable reason is required.")
    return reason.strip()


def lock_person(participant_id):
    person = Participant.objects.select_for_update().get(pk=participant_id, kind="person")
    if canonical_id(person.pk) != person.pk:
        raise ValidationError("This is a reviewed linked identity. Open its canonical profile before editing.")
    if person.archived_at:
        raise ValidationError("Archived participant records are read-only.")
    return person


def profile_revision(person):
    career = ParticipantCareerProfile.objects.filter(participant=person).first()
    return person.updated_at.isoformat() + "|" + (career.updated_at.isoformat() if career else "none")


@transaction.atomic
def save_profile(*, actor, participant_id, person_data, career_data, revision, reason):
    require_people(actor, "participants.change_participant")
    reason = require_reason(reason)
    person = lock_person(participant_id)
    career = ParticipantCareerProfile.objects.select_for_update().filter(participant=person).first()
    if revision != person.updated_at.isoformat() + "|" + (career.updated_at.isoformat() if career else "none"):
        raise ValidationError("This profile changed while you were editing. Reload before saving.")
    before = {"person": snapshot(person, PERSON_FIELDS), "career": snapshot(career, CAREER_FIELDS) if career else {}}
    updates = {key: person_data[key] for key in PERSON_FIELDS}
    updates.update({key: career_data[key] for key in CAREER_FIELDS})
    person = update_participant_profile(participant=person, updates=updates)
    career = ParticipantCareerProfile.objects.get(participant=person)
    audit(actor, "profile.updated", person, before=before,
          after={"person": snapshot(person, PERSON_FIELDS), "career": snapshot(career, CAREER_FIELDS)}, reason=reason)
    return person


def duplicate_fingerprint(matches):
    return hashlib.sha256(",".join(str(pk) for pk in sorted(matches.values_list("pk", flat=True))).encode()).hexdigest()


@transaction.atomic
def create_person(*, actor, data):
    require_people(actor, "participants.add_participant")
    reason = require_reason(data["audit_reason"])
    # Contact values remain hints; staff must explicitly review a separate-person
    # decision rather than silently linking somebody by email or phone.
    matches = participant_candidates(email=data.get("contact_email", ""), phone=data.get("contact_phone", ""))
    if matches.exists():
        if not data.get("confirm_distinct") or data.get("duplicate_revision") != duplicate_fingerprint(matches):
            raise ValidationError("Review the possible matches. Open an existing profile or explicitly confirm a different person.")
    person = Participant(**{key: data.get(key, "") for key in PERSON_FIELDS})
    person.full_clean()
    person.save()
    audit(actor, "profile.created", person, after={"identity_uuid": str(person.identity_uuid),
          "distinct_person_confirmed": bool(matches.exists())}, reason=reason)
    initial_acquisition(participant=person, channel="unknown", provenance="Staff-created record; original source not asserted", actor=actor)
    return person


@transaction.atomic
def initial_acquisition(*, participant, channel, acquired_at=None, event=None, online_source="", source_submission=None, provenance="", actor=None):
    # The caller supplies actual first-creation evidence, never a guessed earliest
    # historical registration. Existing origins are stable across subsequent activity.
    Participant.objects.select_for_update().get(pk=participant.pk)
    existing = ParticipantAcquisition.objects.filter(participant=participant).first()
    if existing:
        return existing
    item = ParticipantAcquisition(participant=participant, channel=channel, acquired_at=acquired_at,
        event=event, online_source=online_source, source_submission=source_submission,
        provenance=provenance, recorded_by=actor if getattr(actor, "is_authenticated", False) else None)
    item.full_clean()
    item.save()
    audit(actor if getattr(actor, "is_authenticated", False) else None, "acquisition.recorded", item,
          after=snapshot(item, ("participant_id", "channel", "acquired_at", "event_id", "online_source", "source_submission_id", "provenance")),
          reason=provenance)
    return item


@transaction.atomic
def correct_acquisition(*, actor, participant_id, data):
    require_people(actor, "participants.manage_participant_acquisition")
    person = lock_person(participant_id)
    item = ParticipantAcquisition.objects.select_for_update().filter(participant=person).first()
    revision = item.updated_at.isoformat() if item else "none"
    if data["revision"] != revision:
        raise ValidationError("Acquisition was corrected by another user. Reload and review the history.")
    before = snapshot(item, ("channel", "acquired_at", "event_id", "online_source", "source_submission_id", "provenance")) if item else {"channel": "unknown"}
    item = item or ParticipantAcquisition(participant=person)
    for key in ("channel", "acquired_at", "event", "online_source", "source_submission", "provenance"):
        setattr(item, key, data[key])
    item.recorded_by = actor
    item.correction_reason = require_reason(data["audit_reason"])
    item.full_clean()
    item.save()
    audit(actor, "acquisition.corrected", item, before=before,
          after=snapshot(item, ("channel", "acquired_at", "event_id", "online_source", "source_submission_id", "provenance")),
          reason=item.correction_reason)
    return item


@transaction.atomic
def save_work(*, actor, participant_id, data, object_id=None):
    require_people(actor, "participants.change_participant")
    person = lock_person(participant_id)
    record = ParticipantWorkExperience.objects.select_for_update().get(pk=object_id, participant=person, archived_at__isnull=True) if object_id else ParticipantWorkExperience(participant=person, created_by=actor)
    if object_id and data.get("revision") != record.updated_at.isoformat():
        raise ValidationError("This work-history entry changed. Reload before saving.")
    fields = ("employer", "role", "location", "started_on", "ended_on", "is_current", "description", "source_reference")
    before = snapshot(record, fields) if object_id else None
    for key in fields:
        setattr(record, key, data[key])
    record.updated_by = actor
    record.full_clean()
    record.save()
    audit(actor, "work.updated" if object_id else "work.created", record, before=before,
          after=snapshot(record, fields), reason=require_reason(data["audit_reason"]))
    return record


def _reject_customer_scope(data):
    if any(data.get(key) for key in ("customer", "customer_id", "organization", "organization_id")) or data.get("audience", "btw_internal") != "btw_internal":
        raise PermissionDenied("Customer-authored notes are not available in Build 11.")


@transaction.atomic
def save_note(*, actor, participant_id, data, object_id=None):
    require_people(actor)
    _reject_customer_scope(data)
    if not can_notes(actor):
        raise PermissionDenied
    person = lock_person(participant_id)
    if object_id:
        note = ParticipantNote.objects.select_for_update().get(pk=object_id, participant=person, archived_at__isnull=True)
        if not can_edit_note(actor, note):
            raise PermissionDenied
        if data.get("revision") != note.updated_at.isoformat():
            raise ValidationError("This note changed while you were editing. Reload before saving.")
        before = {"content": note.content}
    else:
        if not actor.has_perm("participants.add_participantnote"):
            raise PermissionDenied
        prior = ParticipantNote.objects.filter(request_key=data["request_key"]).first()
        if prior:
            if prior.participant_id != person.pk or prior.author_id != actor.pk or prior.content != data["content"]:
                raise PermissionDenied
            return prior
        note = ParticipantNote(participant=person, author=actor, request_key=data["request_key"])
        before = None
    note.content = data["content"]
    note.edited_by = actor if object_id else None
    note.full_clean()
    note.save()
    audit(actor, "note.edited" if object_id else "note.created", note, before=before,
          after={"content": note.content, "audience": "btw_internal"}, reason=require_reason(data["audit_reason"]))
    return note


@transaction.atomic
def archive_record(*, actor, participant_id, kind, object_id, revision, reason, restore=False):
    require_people(actor)
    person = lock_person(participant_id)
    model = ParticipantNote if kind == "note" else ParticipantWorkExperience
    row = model.objects.select_for_update().get(pk=object_id, participant=person)
    if kind == "note":
        if not can_edit_note(actor, row):
            raise PermissionDenied
    elif not actor.has_perm("participants.change_participant"):
        raise PermissionDenied
    if revision != row.updated_at.isoformat():
        raise ValidationError("This record changed. Reload before proceeding.")
    row.archived_at = None if restore else timezone.now()
    row.save(update_fields=["archived_at", "updated_at"])
    audit(actor, kind + (".restored" if restore else ".archived"), row,
          after={"archived": not restore}, reason=require_reason(reason))
    return row
