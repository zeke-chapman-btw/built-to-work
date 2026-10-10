"""Explicit, audited consent-document publication workflow in Django Admin."""
import hashlib

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.core.audit import record_audit
from .consent_documents import extract_consent_pdf
from .models import ConsentDocumentVersion


class ConsentUploadForm(forms.Form):
    key = forms.SlugField(initial='btw-intake', max_length=80)
    version = forms.CharField(max_length=40)
    title = forms.CharField(max_length=200)
    original_pdf = forms.FileField(label='Original PDF')

    def clean_original_pdf(self):
        upload = self.cleaned_data['original_pdf']
        self.extracted_text, self.content_hash = extract_consent_pdf(upload)
        return upload

    def clean(self):
        data = super().clean()
        if data.get('key') and data.get('version') and ConsentDocumentVersion.objects.filter(
            key=data['key'], version=data['version']
        ).exists():
            raise forms.ValidationError('This document version already exists.')
        return data


class ConsentAdminWorkflowMixin:
    change_list_template = 'admin/participants/consentdocumentversion/change_list.html'
    list_display = ('key', 'version', 'title', 'is_approved', 'published_at', 'published_by', 'content_hash', 'workflow_link')
    readonly_fields = ('key', 'version', 'title', 'body', 'original_pdf', 'content_hash',
                       'is_approved', 'effective_at', 'text_verified_by', 'text_verified_at',
                       'published_by', 'published_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _require_admin(self, request):
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied

    def _url(self, name, document=None):
        kwargs = {'document_id': document.pk} if document else None
        return reverse('admin:participants_consentdocumentversion_' + name, kwargs=kwargs)

    def workflow_link(self, document):
        return format_html('<a href="{}">Review / preview</a>', self._url('review', document))
    workflow_link.short_description = 'Workflow'

    def get_urls(self):
        custom = [
            path('upload-pdf/', self.admin_site.admin_view(self.upload_view), name='participants_consentdocumentversion_upload'),
            path('<int:document_id>/review-text/', self.admin_site.admin_view(self.review_view), name='participants_consentdocumentversion_review'),
            path('<int:document_id>/participant-preview/', self.admin_site.admin_view(self.preview_view), name='participants_consentdocumentversion_preview'),
            path('<int:document_id>/original-pdf/', self.admin_site.admin_view(self.pdf_view), name='participants_consentdocumentversion_pdf'),
            path('<int:document_id>/publish/', self.admin_site.admin_view(self.publish_view), name='participants_consentdocumentversion_publish'),
        ]
        return custom + super().get_urls()

    def upload_view(self, request):
        self._require_admin(request)
        form = ConsentUploadForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            document = ConsentDocumentVersion.objects.create(
                key=data['key'], version=data['version'], title=data['title'],
                body=form.extracted_text, original_pdf=data['original_pdf'],
                content_hash=form.content_hash,
            )
            record_audit(actor=request.user, action='consent.document.uploaded', instance=document,
                         new_data={'version': document.version, 'content_hash': document.content_hash})
            messages.success(request, 'PDF stored as a draft. Review its text before publishing.')
            return redirect(self._url('review', document))
        return render(request, 'admin/participants/consentdocumentversion/upload.html',
                      {'form': form, 'title': 'Upload consent PDF', 'opts': self.model._meta})

    def review_view(self, request, document_id):
        self._require_admin(request)
        document = get_object_or_404(ConsentDocumentVersion, pk=document_id)
        if request.method == 'POST':
            if document.is_approved:
                return HttpResponseBadRequest('Published versions cannot be edited.')
            action = request.POST.get('action')
            if action == 'save':
                body = request.POST.get('body', '').strip()
                if not body or len(body) > 1_000_000:
                    return HttpResponseBadRequest('Enter the complete document text.')
                document.body = body
                document.text_verified_by = None
                document.text_verified_at = None
                document.save(update_fields=['body', 'text_verified_by', 'text_verified_at', 'updated_at'])
                record_audit(actor=request.user, action='consent.document.text_updated', instance=document)
                messages.success(request, 'Text saved. Preview the participant view, then confirm it matches the PDF.')
                return redirect(self._url('preview', document))
            if action == 'verify':
                expected = hashlib.sha256(document.body.encode('utf-8')).hexdigest()
                if request.POST.get('checked_against_pdf') != 'on' or request.session.get(f'consent_preview_{document.pk}') != expected:
                    return HttpResponseBadRequest('Preview the current text and confirm it against the PDF first.')
                document.text_verified_by = request.user
                document.text_verified_at = timezone.now()
                document.save(update_fields=['text_verified_by', 'text_verified_at', 'updated_at'])
                record_audit(actor=request.user, action='consent.document.text_verified', instance=document)
                messages.success(request, 'Displayed text verified against the PDF. You may now publish.')
                return redirect(self._url('review', document))
            return HttpResponseBadRequest('Unknown action.')
        return render(request, 'admin/participants/consentdocumentversion/review.html',
                      {'document': document, 'title': 'Review consent document', 'opts': self.model._meta,
                       'pdf_url': self._url('pdf', document), 'preview_url': self._url('preview', document),
                       'publish_url': self._url('publish', document)})

    def preview_view(self, request, document_id):
        self._require_admin(request)
        document = get_object_or_404(ConsentDocumentVersion, pk=document_id)
        request.session[f'consent_preview_{document.pk}'] = hashlib.sha256(document.body.encode('utf-8')).hexdigest()
        return render(request, 'admin/participants/consentdocumentversion/preview.html',
                      {'document': document, 'review_url': self._url('review', document)})

    def pdf_view(self, request, document_id):
        self._require_admin(request)
        document = get_object_or_404(ConsentDocumentVersion, pk=document_id)
        if not document.original_pdf:
            return HttpResponseBadRequest('No original PDF is stored.')
        return FileResponse(document.original_pdf.open('rb'), content_type='application/pdf',
                            as_attachment=False, filename='consent-original.pdf')

    def publish_view(self, request, document_id):
        self._require_admin(request)
        if request.method != 'POST' or request.POST.get('confirm_publish') != 'on':
            return HttpResponseBadRequest('Explicit publication confirmation is required.')
        with transaction.atomic():
            document = get_object_or_404(ConsentDocumentVersion.objects.select_for_update(), pk=document_id)
            if document.is_approved or not all((document.original_pdf, document.content_hash,
                                                document.body.strip(), document.text_verified_at)):
                return HttpResponseBadRequest('A verified draft PDF and text are required.')
            document.is_approved = True
            document.effective_at = timezone.now()
            document.published_at = document.effective_at
            document.published_by = request.user
            document.save(update_fields=['is_approved', 'effective_at', 'published_at', 'published_by', 'updated_at'])
            record_audit(actor=request.user, action='consent.document.published', instance=document,
                         new_data={'version': document.version, 'content_hash': document.content_hash})
        messages.success(request, 'Consent document version published.')
        return redirect(self._url('review', document))
