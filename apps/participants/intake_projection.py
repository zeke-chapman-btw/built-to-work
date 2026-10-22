"""Attributable projection from retained answers; no history rewriting."""
from datetime import date
import hashlib
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_audit
from .models import (Participant, ParticipantCareerProfile, IntakeFieldState,
                     ParticipantWorkExperience, ParticipantHandsOnExperience)
from .identity import canonical_id
from .intake_contracts import canonical_json, DESTINATIONS, canonical_answers
from .intake_model_guards import intake_profile_write
from .qualification_services import process_frozen_intake_submission

PERSON_FIELDS = {"first_name": "first_name", "last_name": "last_name", "preferred_name": "preferred_name",
                 "dob": "date_of_birth", "email": "contact_email", "phone": "contact_phone",
                 "street_address": "address_line_1", "city": "city", "state": "state",
                 "postal_code": "postal_code", "current_situation": "life_stage"}
LEGACY_GROUPS = {"industry_interests": "career_interests", "travel_willingness": "willing_to_travel",
                 "travel_range": "willing_to_travel", "career_timeline": "work_availability",
                 "opportunity_types": "preferred_work_type", "education_level": "education_training",
                 "school_name": "education_training", "graduation": "education_training",
                 "training": "education_training", "school_activities": "education_training"}


def _serial(value):
    return value.isoformat() if isinstance(value, date) else value


def _digest(value):
    return hashlib.sha256(canonical_json(_serial(value)).encode()).hexdigest()


def _eligible(*, person, career, destination, current, exists, source, is_new):
    state = IntakeFieldState.objects.select_for_update(of=("self",)).select_related("source_submission").filter(
        participant=person, destination=destination).first()
    if state:
        if state.is_protected or state.value_hash != _digest(current):
            if not state.is_protected:
                state.is_protected = True
                state.save(update_fields=["is_protected", "updated_at"])
            return False
        if (state.source_submission.submitted_at and source.submitted_at
                and state.source_submission.submitted_at > source.submitted_at):
            return False
        return True
    if is_new:
        return True
    if destination in PERSON_FIELDS or exists:
        return False
    legacy = LEGACY_GROUPS.get(destination)
    if legacy and getattr(career, legacy) not in (None, ""):
        return False
    return True


def _record(person, destination, value, source):
    IntakeFieldState.objects.update_or_create(
        participant=person, destination=destination,
        defaults={"source_submission": source, "last_value": _serial(value),
                  "value_hash": _digest(value), "is_protected": destination == "dob"})


@transaction.atomic
def project_intake(*, submission, target=None, is_new=False, actor=None):
    """Update eligible current facts and append original-source-owned evidence."""
    if not submission.intake_operation_id:
        raise ValidationError("An immutable Build 11B submission is required.")
    person = Participant.objects.select_for_update(of=("self",)).get(pk=(target or submission.participant).pk)
    if canonical_id(submission.participant_id) != person.pk:
        raise ValidationError("The intake is not associated with this canonical participant.")
    if person.archived_at or person.kind != "person":
        raise ValidationError("An active person is required.")
    if is_new and person.pk != submission.participant_id:
        raise ValidationError("An established target cannot be treated as a newly created source.")
    with intake_profile_write():
        career, _ = ParticipantCareerProfile.objects.get_or_create(participant=person)
    career = ParticipantCareerProfile.objects.select_for_update(of=("self",)).get(pk=career.pk)
    raw_answers = submission.answers
    answers = canonical_answers(submission.form_version.snapshot, raw_answers)
    states = submission.intake_metadata.get("answer_states", {})
    definitions = submission.form_version.snapshot["questions"]
    changed, protected = [], []
    data = dict(career.intake_data)
    if is_new:
        # An optional unanswered field on an intake-created person is known to be
        # unreviewed. Legacy blanks are not given this ownership retrospectively.
        for destination, field in PERSON_FIELDS.items():
            value = _serial(getattr(person, field))
            minimal = {"first_name", "last_name", "preferred_name", "dob", "email", "phone"}
            expected = (answers.get(destination) or "") if destination in minimal else ""
            IntakeFieldState.objects.get_or_create(participant=person, destination=destination,
                defaults={"source_submission": submission, "last_value": value,
                          "value_hash": _digest(value),
                          "is_protected": destination == "dob" or value != expected})
    blocked_travel = False
    for question in definitions:
        key, destination = question["key"], question["destination"]
        if destination in {"submission_only", "consent", "experiences"} or states.get(key) != "answered":
            continue
        if destination not in DESTINATIONS:
            raise ValidationError("Unsupported pinned destination.")
        value = raw_answers.get(key)
        if value is None:
            continue
        field = PERSON_FIELDS.get(destination)
        current = getattr(person, field) if field else data.get(destination)
        eligible = _eligible(person=person, career=career, destination=destination,
                             current=current, exists=(True if field else destination in data),
                             source=submission, is_new=is_new)
        if destination == "travel_range" and blocked_travel:
            eligible = False
        if not eligible:
            protected.append(destination)
            blocked_travel = blocked_travel or destination == "travel_willingness"
            continue
        if field:
            setattr(person, field, date.fromisoformat(value) if destination == "dob" else value)
        else:
            data[destination] = value
            if destination == "industry_interests":
                labels = {item["value"]: item["label"] for item in question["options"]}
                data["_industry_search"] = "; ".join(labels.get(v, v) for v in value)
        _record(person, destination, value, submission)
        changed.append(destination)
    career.intake_data = data
    with intake_profile_write():
        person.full_clean()
        person.save()
        career.full_clean()
        career.save()
    source_person = submission.participant
    if any(answers.get(k) for k in ("job_title", "job_responsibilities", "employment_duration")):
        entry_id = uuid.uuid5(submission.intake_operation_id, "current_employment")
        ParticipantWorkExperience.objects.get_or_create(
            source_submission=submission, source_entry_id=entry_id,
            defaults={"participant": source_person, "source": "intake",
                      "source_reference": f"Intake {submission.intake_operation_id}",
                      "role": answers.get("job_title") or "", "employer": "",
                      "is_current": True, "reported_duration": answers.get("employment_duration") or "",
                      "description": answers.get("job_responsibilities") or "",
                      "created_by": actor if getattr(actor, "is_authenticated", False) else None,
                      "updated_by": actor if getattr(actor, "is_authenticated", False) else None})
    definition = next(q for q in definitions if q["key"] == "experiences")
    for entry in answers.get("experiences") or []:
        ParticipantHandsOnExperience.objects.get_or_create(
            source_submission=submission, entry_id=entry["entry_id"],
            defaults={"participant": source_person, "area": entry["area"],
                      "experience_source": entry["source"], "paid": entry["paid"],
                      "duration": entry["duration"], "practiced_skills": entry.get("skills") or [],
                      "proficiency": entry.get("proficiency") or "", "description": entry.get("description") or "",
                      "definition_snapshot": definition})
    qualifications = process_frozen_intake_submission(submission)
    record_audit(actor=actor if getattr(actor, "is_authenticated", False) else None,
                 action="intake.profile_projected", instance=submission, source="smart_intake",
                 new_data={"target_id": person.pk, "eligible_destinations": changed,
                           "protected_destinations": protected, "created_qualification_count": len(qualifications)})
    return {"updated": changed, "protected": protected}


def protect_manual_person(instance, destinations):
    if not destinations:
        return
    IntakeFieldState.objects.filter(participant=instance, destination__in=destinations,
                                    is_protected=False).update(is_protected=True, updated_at=timezone.now())
    source = instance.registration_submissions.filter(intake_operation_id__isnull=False).order_by("-submitted_at", "-pk").first()
    for destination in destinations:
        value = _serial(getattr(instance, PERSON_FIELDS[destination]))
        IntakeFieldState.objects.get_or_create(participant=instance, destination=destination,
            defaults={"source_submission": source, "last_value": value,
                      "value_hash": _digest(value), "is_protected": True})


def protect_manual_career(instance, destinations, *, legacy_values=None):
    if not destinations:
        return
    IntakeFieldState.objects.filter(participant_id=instance.participant_id, is_protected=False,
                                   destination__in=destinations).update(is_protected=True, updated_at=timezone.now())
    source = instance.participant.registration_submissions.filter(intake_operation_id__isnull=False).order_by("-submitted_at", "-pk").first()
    legacy_values = legacy_values or {}
    for destination in destinations:
        if (destination not in DESTINATIONS or destination in PERSON_FIELDS
                or destination in {"submission_only", "consent", "experiences"}):
            continue
        value = instance.intake_data.get(destination)
        marker = ({"manual_legacy_field": LEGACY_GROUPS[destination], "manual_legacy_value": legacy_values[destination]}
                  if destination in legacy_values else {"manual_typed_value": value})
        state, created = IntakeFieldState.objects.get_or_create(participant=instance.participant, destination=destination,
            defaults={"source_submission": source, "last_value": marker,
                      "value_hash": _digest(value), "is_protected": True})
        if not created:
            state.last_value, state.value_hash, state.is_protected = marker, _digest(value), True
            state.save(update_fields=["last_value", "value_hash", "is_protected", "updated_at"])
