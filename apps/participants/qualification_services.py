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
