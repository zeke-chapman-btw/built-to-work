import os
from pathlib import Path
from django import forms
from django.conf import settings
from django.db.models import Q
from .participant_document_models import ParticipantDocument, ParticipantDocumentCategory

_ALLOWED = {
    '.pdf': (b'%PDF-', 'application/pdf'),
    '.png': (b'\x89PNG\r\n\x1a\n', 'image/png'),
    '.jpg': (b'\xff\xd8\xff', 'image/jpeg'),
    '.jpeg': (b'\xff\xd8\xff', 'image/jpeg'),
}

def validate_document_file(upload):
    if not upload:
        raise forms.ValidationError('Choose a PDF, PNG, or JPEG document.')
    limit = int(getattr(settings, 'PARTICIPANT_DOCUMENT_MAX_UPLOAD_BYTES', 20 * 1024 * 1024))
    if upload.size <= 0 or upload.size > limit:
        raise forms.ValidationError(f'The file must be between 1 byte and {limit // (1024 * 1024)} MB.')
    name = Path(os.path.basename(upload.name)).name
    expected = _ALLOWED.get(Path(name).suffix.lower())
    signature = upload.read(16)
    upload.seek(0)
    if not expected or not signature.startswith(expected[0]):
        raise forms.ValidationError('File contents must match a PDF, PNG, or JPEG extension.')
    return name, expected[1]

class ParticipantDocumentForm(forms.ModelForm):
    file = forms.FileField(required=False)
    audit_reason = forms.CharField(max_length=500, label='Reason', help_text='Required for the audit history.')
    class Meta:
        model = ParticipantDocument
        fields = ('category', 'description', 'issue_date', 'expiration_date', 'participant_visible')
        widgets = {'issue_date': forms.DateInput(attrs={'type': 'date'}), 'expiration_date': forms.DateInput(attrs={'type': 'date'})}
    def __init__(self, *args, participant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.participant = participant
        qs = ParticipantDocumentCategory.objects.filter(is_active=True)
        if self.instance and self.instance.pk:
            qs = ParticipantDocumentCategory.objects.filter(Q(is_active=True) | Q(pk=self.instance.category_id))
        self.fields['category'].queryset = qs
    def clean_file(self):
        upload = self.cleaned_data.get('file')
        if upload:
            self.validated_filename, self.validated_content_type = validate_document_file(upload)
            return upload
        if not (self.instance and self.instance.pk):
            raise forms.ValidationError('A document file is required.')
        return None
    def clean(self):
        data = super().clean()
        issue, expiry = data.get('issue_date'), data.get('expiration_date')
        if issue and expiry and expiry < issue:
            self.add_error('expiration_date', 'Expiration cannot be before the issue date.')
        if not (data.get('audit_reason') or '').strip():
            self.add_error('audit_reason', 'Enter a reason for this document action.')
        return data

class DocumentCategoryForm(forms.ModelForm):
    class Meta:
        model = ParticipantDocumentCategory
        fields = ('name', 'slug', 'description', 'position', 'is_active')
        labels = {'slug': 'Category code', 'position': 'Display order', 'is_active': 'Active'}
        help_texts = {'slug': 'Use a unique short code, such as training.'}
