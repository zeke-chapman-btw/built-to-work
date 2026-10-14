from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from uuid import UUID

from apps.core.audit import record_audit
from apps.participants.models import Participant
from apps.participants.qualification_models import ParticipantQualification, QualificationCatalogEntry


class ParticipantQualificationForm(forms.ModelForm):
    audit_reason = forms.CharField(max_length=500, label="Reason for this change")

    class Meta:
        model = ParticipantQualification
        fields = ("catalog_entry", "custom_name", "experience_details", "years_of_experience", "notes", "is_self_reported")
        widgets = {"experience_details": forms.Textarea(attrs={"rows": 3}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = QualificationCatalogEntry.objects.filter(is_active=True)
        if self.instance.pk and self.instance.catalog_entry_id:
            choices = QualificationCatalogEntry.objects.filter(Q(is_active=True) | Q(pk=self.instance.catalog_entry_id))
        self.fields["catalog_entry"].queryset = choices.order_by("name")
        self.fields["catalog_entry"].required = False
        self.fields["custom_name"].required = False


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
            criteria |= Q(pk=UUID(query))
        except (ValueError, TypeError):
            pass
        participants = Participant.objects.filter(criteria).order_by("last_name", "first_name")[:50]
    return render(request, "participants/staff_qualifications.html", {"query": query, "participants": participants, "mode": "directory"})


@login_required(login_url="admin:login")
def participant_qualifications(request, participant_id, qualification_id=None):
    if not _has_staff_permissions(request.user, "participants.view_participant", "participants.view_participantqualification"):
        raise PermissionDenied
    participant = get_object_or_404(Participant, pk=participant_id)
    records = participant.qualifications.select_related("catalog_entry", "source_submission", "source_question").order_by("-created_at")
    is_edit = qualification_id is not None
    qualification = None
    if is_edit:
        if not request.user.has_perm("participants.change_participantqualification"):
            raise PermissionDenied
        qualification = get_object_or_404(records, pk=qualification_id)
    elif request.method == "POST" and not request.user.has_perm("participants.add_participantqualification"):
        raise PermissionDenied

    form_instance = qualification or ParticipantQualification(participant=participant, source=ParticipantQualification.Source.STAFF)
    form = ParticipantQualificationForm(request.POST or None, instance=form_instance)
    can_add = request.user.has_perm("participants.add_participantqualification")
    can_change = request.user.has_perm("participants.change_participantqualification")
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            old_data = _snapshot(qualification) if qualification else None
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
        return redirect("participants:staff-participant-qualifications", participant_id=participant.pk)

    context = {"participant": participant, "records": records, "form": form, "is_edit": is_edit, "qualification": qualification, "can_add": can_add, "can_change": can_change, "mode": "participant"}
    return render(request, "participants/staff_qualifications.html", context, status=400 if request.method == "POST" and form.errors else 200)
