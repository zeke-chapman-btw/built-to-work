from django.utils import timezone
from .participant_document_models import ParticipantDocument
from .qualification_models import ParticipantQualification
from .verification_models import ParticipantVerificationRecord


def qualification_snapshot(qualification):
    return {
        "catalog_entry_id": qualification.catalog_entry_id,
        "years_of_experience": qualification.years_of_experience,
        "notes": qualification.notes,
        "source": qualification.source,
        "is_self_reported": qualification.is_self_reported,
        "source_question": qualification.source_question,
        "source_question_key": qualification.source_question_key,
        "source_answer_hash": qualification.source_answer_hash,
        "source_answer_snapshot": qualification.source_answer_snapshot,
        "mapping_snapshot": qualification.mapping_snapshot,
    }


def latest_verification(*, qualification=None, document=None):
    records = ParticipantVerificationRecord.objects.select_related("reviewer", "evidence_document", "document")
    if qualification is not None:
        return records.filter(qualification=qualification).order_by("-created_at", "-pk").first()
    return records.filter(document=document).order_by("-created_at", "-pk").first()


def qualification_verification_summary(qualification):
    record = latest_verification(qualification=qualification)
    status = "pending"
    if record:
        unchanged = record.qualification_snapshot == qualification_snapshot(qualification)
        if not unchanged:
            status = "pending"
        elif record.status == ParticipantVerificationRecord.Status.VERIFIED:
            evidence = record.evidence_document
            if record.method == ParticipantVerificationRecord.Method.DOCUMENT_VERIFIED:
                if not evidence or evidence.state != ParticipantDocument.State.ACTIVE:
                    status = "pending"
                elif evidence.expiration_date and evidence.expiration_date < timezone.localdate():
                    status = "expired"
                else:
                    status = "verified"
            else:
                status = "verified"
        else:
            status = record.status
    elif qualification.is_self_reported:
        status = "self_reported"
    return {
        "status": status,
        "method": record.get_method_display() if record else ("Self-reported" if qualification.is_self_reported else "Not yet reviewed"),
        "reviewer": record.reviewer if record else None,
        "reviewed_at": record.reviewed_at if record else None,
        "reason": record.reason if record else "",
        "record": record,
    }


def document_verification_summary(document):
    if document.state != ParticipantDocument.State.ACTIVE:
        return {"status": document.get_state_display().lower(), "method": "", "reviewer": None, "reviewed_at": None, "reason": "", "record": latest_verification(document=document)}
    if document.expiration_date and document.expiration_date < timezone.localdate():
        status = "expired"
    else:
        record = latest_verification(document=document)
        status = record.status if record else "pending"
        if record and record.status == ParticipantVerificationRecord.Status.VERIFIED and document.expiration_date and document.expiration_date < timezone.localdate():
            status = "expired"
        return {
            "status": status,
            "method": record.get_method_display() if record else "Not yet reviewed",
            "reviewer": record.reviewer if record else None,
            "reviewed_at": record.reviewed_at if record else None,
            "reason": record.reason if record else "",
            "record": record,
        }
    record = latest_verification(document=document)
    return {"status": status, "method": record.get_method_display() if record else "Not yet reviewed", "reviewer": record.reviewer if record else None, "reviewed_at": record.reviewed_at if record else None, "reason": record.reason if record else "", "record": record}


def can_view_qualification_approvals(user):
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or (user.is_staff and (user.has_perm("participants.view_qualificationapprovals") or user.has_perm("participants.review_qualificationapprovals")))))


def can_review_qualification_approvals(user):
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or (user.is_staff and user.has_perm("participants.review_qualificationapprovals"))))
