import uuid
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .participant_document_models import ParticipantDocument
from .qualification_models import ParticipantQualification
from .verification_forms import VerificationDecisionForm
from .verification_models import ParticipantVerificationRecord
from .verification_services import (can_review_qualification_approvals, can_view_qualification_approvals,
    document_verification_summary, latest_verification, qualification_snapshot, qualification_verification_summary)


def _target(kind, object_id):
    if kind == "qualification":
        return get_object_or_404(ParticipantQualification.objects.select_related("participant", "catalog_entry"), pk=object_id, is_active=True)
    if kind == "document":
        return get_object_or_404(ParticipantDocument.objects.select_related("participant", "category"), pk=object_id)
    return None


def _summary(kind, target):
    return qualification_verification_summary(target) if kind == "qualification" else document_verification_summary(target)


@require_http_methods(["GET"])
def qualification_approvals(request):
    if not can_view_qualification_approvals(request.user):
        return HttpResponseForbidden("You are not authorized to view qualification approvals.")
    pending = []
    for qualification in ParticipantQualification.objects.filter(is_active=True).select_related("participant", "catalog_entry").order_by("participant__last_name", "participant__first_name"):
        summary = qualification_verification_summary(qualification)
        if summary["status"] in ("pending", "self_reported", "expired"):
            pending.append({"kind": "qualification", "target": qualification, "summary": summary})
    for document in ParticipantDocument.objects.filter(state=ParticipantDocument.State.ACTIVE).select_related("participant", "category").order_by("participant__last_name", "participant__first_name"):
        summary = document_verification_summary(document)
        if summary["status"] in ("pending", "expired"):
            pending.append({"kind": "document", "target": document, "summary": summary})
    return render(request, "participants/qualification_approvals.html", {"pending_items": pending, "can_decide": can_review_qualification_approvals(request.user), "page_title": "Approvals", "page_description": "Review participant qualifications and supporting documents."})


@require_http_methods(["GET", "POST"])
def qualification_approval_detail(request, kind, object_id):
    if not can_view_qualification_approvals(request.user):
        return HttpResponseForbidden("You are not authorized to view qualification approvals.")
    target = _target(kind, object_id)
    if target is None:
        return HttpResponseBadRequest("Unknown verification target.")
    participant = target.participant
    summary = _summary(kind, target)
    history = ParticipantVerificationRecord.objects.filter(**{kind: target}).select_related("reviewer", "evidence_document", "supersedes")
    form = VerificationDecisionForm(request.POST or None, participant=participant, target_kind=kind, initial={"request_key": uuid.uuid4()})
    if request.method == "POST":
        if not can_review_qualification_approvals(request.user):
            return HttpResponseForbidden("You are not authorized to decide approval requests.")
        if form.is_valid() and kind == "document" and form.cleaned_data["status"] == "verified" and target.expiration_date and target.expiration_date < timezone.localdate():
            form.add_error(None, "Expired documents cannot be verified.")
        elif form.is_valid():
            key = form.cleaned_data["request_key"]
            previous_request = ParticipantVerificationRecord.objects.filter(request_key=key).first()
            if previous_request:
                if previous_request.participant_id != participant.pk or getattr(previous_request, f"{kind}_id") != target.pk:
                    return HttpResponseForbidden("This review request key is already associated with another record.")
                messages.success(request, "This decision was already recorded.")
                return redirect("participants:staff-qualification-approvals")
            try:
                with transaction.atomic():
                    locked = type(target).objects.select_for_update().get(pk=target.pk)
                    prior = latest_verification(**{kind: locked})
                    record = ParticipantVerificationRecord(
                        participant=participant, qualification=locked if kind == "qualification" else None,
                        document=locked if kind == "document" else None,
                        evidence_document=form.cleaned_data.get("evidence_document"),
                        status=form.cleaned_data["status"], method=form.cleaned_data["method"],
                        evidence_notes=form.cleaned_data["evidence_notes"].strip(), reason=form.cleaned_data["reason"].strip(),
                        reviewer=request.user, reviewed_at=timezone.now(), supersedes=prior, request_key=key,
                        qualification_snapshot=qualification_snapshot(locked) if kind == "qualification" else {},
                    )
                    record.full_clean()
                    record.save()
            except IntegrityError:
                existing = ParticipantVerificationRecord.objects.filter(request_key=key).first()
                if not existing:
                    raise
                messages.success(request, "This decision was already recorded.")
                return redirect("participants:staff-qualification-approvals")
            messages.success(request, "Verification decision recorded.")
            return redirect("participants:staff-qualification-approvals")
    return render(request, "participants/qualification_approval_detail.html", {
        "kind": kind, "target": target, "participant": participant, "summary": summary,
        "history": history, "form": form, "can_decide": can_review_qualification_approvals(request.user),
        "participant_qualification_url": reverse("participants:staff-participant-qualifications", kwargs={"participant_id": participant.pk}),
        "page_title": "Review verification", "page_description": str(participant),
    }, status=400 if request.method == "POST" and form.errors else 200)
