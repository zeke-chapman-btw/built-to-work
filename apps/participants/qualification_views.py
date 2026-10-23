from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Prefetch
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from apps.internal_ui.people_editors import people_render as render, people_saved
from uuid import UUID

from apps.core.audit import record_audit
from apps.participants.models import Participant
from apps.participants.qualification_models import ParticipantQualification, QualificationCatalogEntry


class ParticipantQualificationForm(forms.ModelForm):
    confirm_verified_change = forms.BooleanField(required=False, label="I understand that changing a verified qualification requires a new review")
    audit_reason = forms.CharField(max_length=500, label="Reason for this change")

    class Meta:
        model = ParticipantQualification
        fields = ("catalog_entry", "custom_name", "experience_details", "years_of_experience", "issue_date", "expiration_date", "notes", "is_self_reported")
        widgets = {"issue_date": forms.DateInput(attrs={"type": "date"}), "expiration_date": forms.DateInput(attrs={"type": "date"}), "experience_details": forms.Textarea(attrs={"rows": 3}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = QualificationCatalogEntry.objects.filter(is_active=True)
        if self.instance.pk and self.instance.catalog_entry_id:
            choices = QualificationCatalogEntry.objects.filter(Q(is_active=True) | Q(pk=self.instance.catalog_entry_id))
        self.fields["catalog_entry"].queryset = choices.order_by("name")
        self.fields["catalog_entry"].required = False
        self.fields["custom_name"].required = False
        self.fields["custom_name"].label = "Custom qualification"
        self.fields["catalog_entry"].label = "Catalog qualification"
        self.fields["custom_name"].help_text = "Use a custom qualification when it is not in the catalog."
        self.fields["experience_details"].label = "Experience and credential details"
    def clean_is_self_reported(self):
        return True if self.instance.source_submission_id else self.cleaned_data["is_self_reported"]


def _has_staff_permissions(user, *permissions):
    return user.is_active and user.is_staff and all(user.has_perm(permission) for permission in permissions)


def _snapshot(record):
    return {
        "participant_id": str(record.participant_id),
        "catalog_entry_id": str(record.catalog_entry_id) if record.catalog_entry_id else None,
        "catalog_entry": record.catalog_entry.name if record.catalog_entry_id else "",
        "custom_name": record.custom_name,
        "experience_details": record.experience_details,
        "years_of_experience": str(record.years_of_experience) if record.years_of_experience is not None else None,
        "issue_date": str(record.issue_date or ""),
        "expiration_date": str(record.expiration_date or ""),
        "notes": record.notes,
        "source": record.source,
        "is_self_reported": record.is_self_reported,
        "source_submission_id": str(record.source_submission_id) if record.source_submission_id else None,
        "source_question_id": record.source_question_id,
        "source_question_key": record.source_question_key,
        "source_answer_hash": record.source_answer_hash,
        "source_answer_snapshot": record.source_answer_snapshot,
        "mapping_snapshot": record.mapping_snapshot,
    }


@login_required(login_url="admin:login")
def qualification_directory(request):
    if not _has_staff_permissions(request.user, "participants.view_participant", "participants.view_participantqualification"):
        raise PermissionDenied
    query = request.GET.get("q", "").strip()
    participants = Participant.objects.none()
    if query:
        criteria = Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(preferred_name__icontains=query) | Q(contact_email__icontains=query) | Q(contact_phone__icontains=query)
        try:
            criteria |= Q(identity_uuid=UUID(query))
        except (ValueError, TypeError):
            pass
        participants = Participant.objects.filter(criteria, kind="person").order_by("last_name", "first_name")[:50]
    return render(request, "participants/staff_qualifications.html", {"query": query, "participants": participants, "mode": "directory", "page_title": "Participant qualifications"})


@login_required(login_url="admin:login")
@transaction.atomic
def participant_qualifications(request, participant_id, qualification_id=None):
    if not _has_staff_permissions(request.user, "participants.view_participant", "participants.view_participantqualification"):
        raise PermissionDenied
    participant = get_object_or_404(Participant, pk=participant_id)
    records = participant.qualifications.select_related("catalog_entry", "source_submission", "source_question").prefetch_related("document_links__document").order_by("-created_at")
    from .verification_models import ParticipantVerificationRecord
    records = records.prefetch_related(Prefetch("verification_records", queryset=ParticipantVerificationRecord.objects.select_related("reviewer", "evidence_document", "document").order_by("-created_at", "-pk"), to_attr="_workspace_verifications"))
    is_edit = qualification_id is not None
    qualification = None
    if is_edit:
        if not request.user.has_perm("participants.change_participantqualification"):
            raise PermissionDenied
        qualification = get_object_or_404(records.select_for_update(of=("self",)) if request.method == "POST" else records, pk=qualification_id)
    elif request.method == "POST" and not request.user.has_perm("participants.add_participantqualification"):
        raise PermissionDenied

    form_instance = qualification or ParticipantQualification(participant=participant, source=ParticipantQualification.Source.STAFF)
    old_data = _snapshot(qualification) if qualification else None
    form = ParticipantQualificationForm(request.POST if request.method == "POST" else None, instance=form_instance)
    can_add = request.user.has_perm("participants.add_participantqualification")
    can_change = request.user.has_perm("participants.change_participantqualification")
    from .verification_services import qualification_verification_summary
    was_verified = bool(qualification and qualification_verification_summary(qualification)["status"] == "verified")
    if not was_verified:
        form.fields.pop("confirm_verified_change", None)
    valid = form.is_valid() if request.method == "POST" else False
    if valid and was_verified and not form.cleaned_data.get("confirm_verified_change"):
        form.add_error("confirm_verified_change", "Confirm that the existing verification will require a new review after a material change.")
        valid = False
    if request.method == "POST" and valid:
        with transaction.atomic():
            saved = form.save(commit=False)
            saved.participant = participant
            saved.updated_by = request.user
            if not qualification:
                saved.source = ParticipantQualification.Source.STAFF
                saved.created_by = request.user
            saved.save()
            record_audit(
                actor=request.user,
                action="qualification.participant_record.update" if qualification else "qualification.participant_record.create",
                instance=saved,
                source="staff_interface",
                old_data=old_data,
                new_data=_snapshot(saved),
                reason=form.cleaned_data["audit_reason"],
            )
        return people_saved(request,"participants:staff-participant-qualifications", participant_id=participant.pk)

    page = Paginator(records, 25).get_page(request.GET.get("page"))
    context = {"participant": participant, "records": page, "page_obj": page, "form": form, "is_edit": is_edit, "qualification": qualification, "can_add": can_add, "can_change": can_change, "mode": "participant", "page_title": "Participant qualifications"}
    return render(request, "participants/staff_qualifications.html", context, status=400 if request.method == "POST" and form.errors else 200)
