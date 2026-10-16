import uuid
from django import forms
from django.utils import timezone
from .participant_document_models import ParticipantDocument
from .verification_models import ParticipantVerificationRecord

class VerificationDecisionForm(forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    status = forms.ChoiceField(choices=(("unverified", "Unverified"), ("pending", "Pending Review"), ("verified", "Verify"), ("rejected", "Reject")))
    method = forms.ChoiceField(choices=())
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), max_length=2000, label="Decision reason")
    evidence_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=4000, label="Supporting evidence")
    evidence_document = forms.ModelChoiceField(queryset=ParticipantDocument.objects.none(), required=False, label="Current supporting document")

    def __init__(self, *args, participant, target_kind, **kwargs):
        super().__init__(*args, **kwargs)
        if target_kind == "document":
            self.fields["method"].choices = (("document_verified", "Document-verified"), ("staff_confirmed", "Staff-confirmed"))
            self.fields.pop("evidence_document")
        else:
            self.fields["method"].choices = (("document_verified", "Document-verified"), ("skill_assessed", "Skill-assessed"), ("staff_confirmed", "Staff-confirmed"))
            self.fields["evidence_document"].queryset = ParticipantDocument.objects.filter(participant=participant, state=ParticipantDocument.State.ACTIVE).order_by("-created_at")

    def clean(self):
        data = super().clean()
        if not (data.get("reason") or "").strip():
            self.add_error("reason", "A reason is required for every review decision.")
        method = data.get("method")
        if data.get("status") == ParticipantVerificationRecord.Status.VERIFIED:
            if method == ParticipantVerificationRecord.Method.DOCUMENT_VERIFIED and "evidence_document" in self.fields and not data.get("evidence_document"):
                self.add_error("evidence_document", "Select an active document version to verify against.")
            if method in (ParticipantVerificationRecord.Method.SKILL_ASSESSED, ParticipantVerificationRecord.Method.STAFF_CONFIRMED) and not (data.get("evidence_notes") or "").strip():
                self.add_error("evidence_notes", "Describe the skill assessment or staff-confirmation evidence.")
        document = data.get("evidence_document")
        if document and document.expiration_date and document.expiration_date < timezone.localdate():
            self.add_error("evidence_document", "An expired document cannot support a new verification.")
        return data
