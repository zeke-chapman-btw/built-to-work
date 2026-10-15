from pathlib import Path
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.participants.models import Participant
from apps.participants.qualification_models import ParticipantQualification
from .participant_document_models import (
    ParticipantDocument, ParticipantDocumentAudit, ParticipantDocumentCategory,
    QualificationDocumentLink,
)
from .document_forms import ParticipantDocumentForm, DocumentCategoryForm

VIEW_PERMS = ('participants.view_participant', 'participants.view_participantdocument')
MANAGE_PERMS = ('participants.view_participant', 'participants.manage_participant_documents')


def _require_staff(user, *perms):
    if not user.is_authenticated or not user.is_staff or not all(user.has_perm(permission) for permission in perms):
        raise PermissionDenied


def _audit(document, actor, action, reason='', details=None):
    return ParticipantDocumentAudit.objects.create(
        document=document, actor=actor, action=action, reason=reason, details=details or {}
    )


def _safe_upload_name(upload, content_type):
    extensions = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg'}
    upload.name = f'document{extensions[content_type]}'
    return upload


@login_required
def document_directory(request):
    _require_staff(request.user, *VIEW_PERMS)
    query = request.GET.get('q', '').strip()
    participants = Participant.objects.all().order_by('last_name', 'first_name')
    if query:
        participants = participants.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(contact_email__icontains=query) | Q(contact_phone__icontains=query)
        )
    return render(request, 'participants/staff_document_directory.html', {
        'participants': participants[:100], 'query': query,
    })


@login_required
def participant_documents(request, participant_id):
    _require_staff(request.user, *VIEW_PERMS)
    participant = get_object_or_404(Participant, pk=participant_id)
    docs = participant.documents.select_related(
        'category', 'uploaded_by', 'replaces'
    ).prefetch_related('qualification_links__qualification__category', 'audit_events__actor')
    form = ParticipantDocumentForm(participant=participant)
    if request.method == 'POST':
        _require_staff(request.user, *MANAGE_PERMS)
        form = ParticipantDocumentForm(request.POST, request.FILES, participant=participant)
        if form.is_valid():
            upload = _safe_upload_name(form.cleaned_data['file'], form.validated_content_type)
            document = form.save(commit=False)
            document.participant = participant
            document.uploaded_by = request.user
            document.upload_source = ParticipantDocument.UploadSource.STAFF
            document.original_filename = form.validated_filename
            document.content_type = form.validated_content_type
            document.file_size = upload.size
            try:
                with transaction.atomic():
                    document.file = upload
                    document.full_clean()
                    document.save()
                    _audit(document, request.user, 'uploaded', form.cleaned_data['audit_reason'], {
                        'size': document.file_size, 'content_type': document.content_type,
                    })
            except Exception:
                if document.file and document.file.name:
                    document.file.storage.delete(document.file.name)
                raise
            messages.success(request, 'Document uploaded securely.')
            return redirect('participants:staff-participant-documents', participant_id=participant.pk)
    qualifications = participant.qualifications.select_related('category').all()
    return render(request, 'participants/staff_documents.html', {
        'participant': participant, 'documents': docs, 'qualifications': qualifications, 'form': form,
    })


@login_required
def document_edit(request, document_uuid):
    _require_staff(request.user, *MANAGE_PERMS)
    document = get_object_or_404(ParticipantDocument.objects.select_related('participant'), uuid=document_uuid)
    if request.method == 'POST' and request.FILES.get('file'):
        raise Http404('Use the replace action to preserve document history.')
    if request.method == 'POST':
        with transaction.atomic():
            locked = get_object_or_404(
                ParticipantDocument.objects.select_for_update().select_related('participant'), uuid=document_uuid
            )
            form = ParticipantDocumentForm(request.POST, instance=locked, participant=locked.participant)
            form.fields.pop('file', None)
            if form.is_valid():
                reason = form.cleaned_data['audit_reason']
                before = {
                    'category': locked.category_id, 'description': locked.description,
                    'issue_date': str(locked.issue_date or ''),
                    'expiration_date': str(locked.expiration_date or ''),
                }
                updated = form.save(commit=False)
                updated.full_clean()
                updated.save()
                after = {
                    'category': updated.category_id, 'description': updated.description,
                    'issue_date': str(updated.issue_date or ''),
                    'expiration_date': str(updated.expiration_date or ''),
                }
                _audit(updated, request.user, 'metadata_changed', reason, {'before': before, 'after': after})
                messages.success(request, 'Document details updated.')
                return redirect('participants:staff-participant-documents', participant_id=locked.participant_id)
            document, form = locked, form
    else:
        form = ParticipantDocumentForm(instance=document, participant=document.participant)
        form.fields.pop('file', None)
    return render(request, 'participants/staff_document_edit.html', {'document': document, 'form': form})


@login_required
@require_POST
def document_archive(request, document_uuid):
    _require_staff(request.user, *MANAGE_PERMS)
    reason = request.POST.get('audit_reason', '').strip()
    if not reason:
        messages.error(request, 'An audit reason is required.')
    else:
        with transaction.atomic():
            document = get_object_or_404(
                ParticipantDocument.objects.select_for_update(), uuid=document_uuid
            )
            if document.state != ParticipantDocument.State.ARCHIVED:
                document.state = ParticipantDocument.State.ARCHIVED
                document.save(update_fields=['state', 'updated_at'])
                _audit(document, request.user, 'archived', reason)
    document = get_object_or_404(ParticipantDocument, uuid=document_uuid)
    return redirect('participants:staff-participant-documents', participant_id=document.participant_id)


@login_required
def document_replace(request, document_uuid):
    _require_staff(request.user, *MANAGE_PERMS)
    previous = get_object_or_404(
        ParticipantDocument.objects.select_related('participant'), uuid=document_uuid
    )
    if previous.state != ParticipantDocument.State.ACTIVE:
        raise Http404('Only the current active document can be replaced.')
    form = ParticipantDocumentForm(request.POST or None, request.FILES or None, participant=previous.participant)
    if request.method == 'POST' and form.is_valid():
        upload = _safe_upload_name(form.cleaned_data['file'], form.validated_content_type)
        try:
            with transaction.atomic():
                previous = get_object_or_404(
                    ParticipantDocument.objects.select_for_update().select_related('participant'),
                    uuid=document_uuid, state=ParticipantDocument.State.ACTIVE,
                )
                replacement = form.save(commit=False)
                replacement.participant = previous.participant
                replacement.uploaded_by = request.user
                replacement.upload_source = ParticipantDocument.UploadSource.STAFF
                replacement.original_filename = form.validated_filename
                replacement.content_type = form.validated_content_type
                replacement.file_size = upload.size
                replacement.replaces = previous
                replacement.file = upload
                replacement.full_clean()
                replacement.save()
                previous.state = ParticipantDocument.State.REPLACED
                previous.save(update_fields=['state', 'updated_at'])
                reason = form.cleaned_data['audit_reason']
                _audit(previous, request.user, 'replaced', reason, {'replacement_uuid': str(replacement.uuid)})
                _audit(replacement, request.user, 'replacement_uploaded', reason, {'replaces_uuid': str(previous.uuid)})
        except Exception:
            if 'replacement' in locals() and replacement.file and replacement.file.name:
                replacement.file.storage.delete(replacement.file.name)
            raise
        messages.success(request, 'Replacement saved; the prior file remains in history.')
        return redirect('participants:staff-participant-documents', participant_id=previous.participant_id)
    return render(request, 'participants/staff_document_replace.html', {'previous': previous, 'form': form})


@login_required
@require_GET
def document_download(request, document_uuid):
    _require_staff(request.user, *VIEW_PERMS)
    document = get_object_or_404(
        ParticipantDocument.objects.select_related('participant'), uuid=document_uuid
    )
    try:
        handle = document.file.open('rb')
    except (FileNotFoundError, OSError, ValueError):
        raise Http404('Document file is unavailable.')
    _audit(document, request.user, 'downloaded')
    response = FileResponse(
        handle, as_attachment=True, filename=Path(document.original_filename).name,
        content_type='application/octet-stream',
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def qualification_document_link(request, document_uuid, qualification_id):
    _require_staff(request.user, *MANAGE_PERMS, 'participants.manage_qualification_document_links')
    document = get_object_or_404(ParticipantDocument, uuid=document_uuid)
    qualification = get_object_or_404(
        ParticipantQualification, pk=qualification_id, participant_id=document.participant_id
    )
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'An audit reason is required to link a document.')
    else:
        with transaction.atomic():
            link, created = QualificationDocumentLink.objects.get_or_create(
                document=document, qualification=qualification,
                defaults={'linked_by': request.user, 'reason': reason},
            )
            if created:
                _audit(document, request.user, 'qualification_linked', reason, {
                    'qualification_id': qualification.pk,
                })
    return redirect('participants:staff-participant-documents', participant_id=document.participant_id)


@login_required
@require_POST
def qualification_document_unlink(request, document_uuid, qualification_id):
    _require_staff(request.user, *MANAGE_PERMS, 'participants.manage_qualification_document_links')
    document = get_object_or_404(ParticipantDocument, uuid=document_uuid)
    link = get_object_or_404(
        QualificationDocumentLink, document=document, qualification_id=qualification_id
    )
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'An audit reason is required to unlink a document.')
    else:
        with transaction.atomic():
            _audit(document, request.user, 'qualification_unlinked', reason, {
                'qualification_id': qualification_id,
            })
            link.delete()
    return redirect('participants:staff-participant-documents', participant_id=document.participant_id)


@login_required
def document_categories(request):
    _require_staff(request.user, 'participants.manage_participant_document_categories')
    category = None
    category_id = request.GET.get('edit') or request.POST.get('category_id')
    if category_id:
        category = get_object_or_404(ParticipantDocumentCategory, pk=category_id)
    form = DocumentCategoryForm(request.POST or None, instance=category)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if not obj.pk:
            obj.created_by = request.user
        obj.updated_by = request.user
        obj.save()
        messages.success(request, 'Document category saved.')
        return redirect('participants:staff-document-categories')
    return render(request, 'participants/staff_document_categories.html', {
        'categories': ParticipantDocumentCategory.objects.all(), 'form': form, 'editing': category,
    })
