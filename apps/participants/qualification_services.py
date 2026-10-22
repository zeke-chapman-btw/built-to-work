"""Exact, repeat-safe intake-to-qualification mapping services."""
import hashlib
import json
from django.db import transaction
from apps.participants.models import ParticipantQualification, RegistrationQualificationMapping


def _answer_hash(mapping, answer):
    payload = {"mapping_id": str(mapping.pk), "question_key": mapping.question_key, "answer_value": mapping.answer_value, "answer": answer}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def process_registration_submission(submission):
    """Apply only explicit published mappings to a submitted registration.

    The mapping and original answer are snapshotted on each derived record. This
    function is safe to retry; it never updates a previously-created record.
    """
    if getattr(submission, "intake_operation_id", None):
        # Build 11B explicitly projects the pinned contract only after identity
        # resolution; this signal must not apply today's mutable draft mappings.
        return []
    if submission.status != "submitted" or not submission.participant_id:
        return []
    form_version = submission.form_version
    form = form_version.form
    answers = submission.answers if isinstance(submission.answers, dict) else {}
    created_records = []
    with transaction.atomic():
        mappings = RegistrationQualificationMapping.objects.select_related(
            "registration_question", "catalog_entry", "form"
        ).filter(form=form, is_active=True, is_published=True)
        for mapping in mappings:
            question = mapping.registration_question
            key = mapping.question_key
            if not question.is_active or key != question.canonical_key or key not in answers:
                continue
            answer = answers[key]
            if mapping.mapping_type == RegistrationQualificationMapping.MappingType.CHOICE:
                values = answer if isinstance(answer, list) else [answer]
                if not any(value == mapping.answer_value for value in values):
                    continue
                custom_name = ""
            elif mapping.mapping_type == RegistrationQualificationMapping.MappingType.CUSTOM_TEXT:
                if question.field_type not in {"short_text", "long_text", "text", "textarea"}:
                    continue
                if not isinstance(answer, str) or not answer.strip():
                    continue
                custom_name = answer.strip()[:200]
            else:
                continue

            digest = _answer_hash(mapping, answer)
            answer_snapshot = {
                "question_key": key,
                "answer": answer,
                "original_answer_snapshot": submission.answer_snapshot,
                "form_version_id": str(submission.form_version_id) if submission.form_version_id else None,
                "submission_id": str(submission.pk),
            }
            mapping_snapshot = {
                "mapping_id": str(mapping.pk),
                "mapping_type": mapping.mapping_type,
                "question_key": key,
                "answer_value": mapping.answer_value,
                "catalog_entry_id": str(mapping.catalog_entry_id) if mapping.catalog_entry_id else None,
                "catalog_stable_id": str(mapping.catalog_entry.stable_id) if mapping.catalog_entry_id else None,
                "catalog_name": mapping.catalog_entry.name if mapping.catalog_entry_id else None,
            }
            record, created = ParticipantQualification.objects.get_or_create(
                source_submission=submission,
                source_question_key=key,
                source_answer_hash=digest,
                defaults={
                    "participant_id": submission.participant_id,
                    "catalog_entry": mapping.catalog_entry,
                    "custom_name": custom_name,
                    "source": ParticipantQualification.Source.REGISTRATION,
                    "is_self_reported": True,
                    "source_answer_snapshot": answer_snapshot,
                    "mapping_snapshot": mapping_snapshot,
                    "is_active": True,
                },
            )
            if created:
                created_records.append(record)
    return created_records


@transaction.atomic
def process_frozen_intake_submission(submission):
    """Reuse qualification records for explicit frozen self-reports only.

    Keep claims on the original source participant. Reviewed alias projection
    supplies canonical visibility without breaking submission ownership.
    """
    from django.core.exceptions import ValidationError
    from .models import QualificationCatalogEntry, RegistrationQuestion
    from .intake_lifecycle import verify_published
    if not submission.intake_operation_id or submission.status != "submitted":
        raise ValidationError("A retained Build 11B intake submission is required.")
    version = verify_published(submission.form_version, allow_retired=True)
    contract = version.snapshot
    answers = submission.answers
    questions = {q.canonical_key: q for q in RegistrationQuestion.objects.filter(form_id=version.form_id)}
    created_records = []
    for mapping in contract["qualification_mappings"]:
        key, expected = mapping["question"], mapping["value"]
        if key == "skills":
            contexts = [(row["entry_id"], row.get("skills") or [], row)
                        for row in answers.get("experiences") or []]
        else:
            value = answers.get(key)
            contexts = [("", value if isinstance(value, list) else [value], {})]
        for entry_id, values, entry in contexts:
            if expected not in values:
                continue
            source_key = f"experiences.{entry_id}.skills" if entry_id else key
            digest_payload = {"operation": str(submission.intake_operation_id),
                              "version": str(version.stable_id), "mapping": mapping,
                              "question": source_key, "answer": expected}
            digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            catalog = QualificationCatalogEntry.objects.get(stable_id=mapping["catalog_uuid"])
            record, created = ParticipantQualification.objects.get_or_create(
                source_submission=submission, source_question_key=source_key,
                source_answer_hash=digest,
                defaults={"participant_id": submission.participant_id, "catalog_entry": catalog,
                          "source": ParticipantQualification.Source.REGISTRATION, "is_self_reported": True,
                          "source_question": questions.get("experiences" if entry_id else key),
                          "source_answer_snapshot": {"question_key": source_key, "answer": expected,
                              "entry": entry, "form_version_uuid": str(version.stable_id),
                              "operation_uuid": str(submission.intake_operation_id)},
                          "mapping_snapshot": mapping, "is_active": True})
            record.full_clean()
            if created:
                created_records.append(record)
    return created_records
