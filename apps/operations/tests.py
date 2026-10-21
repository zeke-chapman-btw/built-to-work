"""Synthetic acceptance tests for tasks, notes, private operational documents and scope."""
import hashlib,tempfile
from datetime import UTC,datetime,date,timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError,transaction
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.staffing.models import StaffProfile,StaffAssignment
from .models import Task,EventNote,EventDocument
from . import services as svc,storage
from .forms import TaskForm

@override_settings(DEBUG=True)
class Operations9DTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model()
        cls.manager=U.objects.create_user('synthetic-9d-manager',password='test-password',is_staff=True)
        cls.employee=U.objects.create_user('synthetic-9d-employee',password='test-password',is_staff=True)
        cls.other=U.objects.create_user('synthetic-9d-other',password='test-password',is_staff=True)
        cls.public=U.objects.create_user('synthetic-9d-public',password='test-password')
        cls.manager.user_permissions.add(*Permission.objects.filter(content_type__app_label='operations',codename__in=['manage_tasks','manage_event_notes','manage_event_documents']))
        cls.manager.user_permissions.add(Permission.objects.get(content_type__app_label='events',codename='view_event'))
        cls.person=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='9D Employee',user=cls.employee,internal_notes='SYNTHETIC PRIVATE STAFF INFORMATION')
        cls.other_person=StaffProfile.objects.create(first_name='SYNTHETIC',last_name='9D Other',user=cls.other)
        cls.event=Event.objects.create(name='SYNTHETIC 9D event',start_at=datetime(2030,7,1,13,tzinfo=UTC),end_at=datetime(2030,7,1,21,tzinfo=UTC),status='upcoming',timezone_name='America/New_York',description='SYNTHETIC CONFIDENTIAL EVENT DESCRIPTION')
        StaffAssignment.objects.create(event=cls.event,staff=cls.person)
    def setUp(self):
        self.login(self.manager)
    def login(self,user):self.client.force_login(user,backend='django.contrib.auth.backends.ModelBackend')
    def task(self,old=None,**changes):
        data=dict(title='SYNTHETIC prepare equipment',description='SYNTHETIC operational instructions',assigned_staff=str(self.person.pk),event='',due_date='2030-07-01',priority='normal',reason='SYNTHETIC task verification',revision=old.updated_at.isoformat() if old else 'new');data.update(changes)
        obj,form=svc.save_task(actor=self.manager,data=data,task_id=old.pk if old else None)
        self.assertIsNotNone(obj,form.errors);return obj
    def status(self,obj,status='completed',actor=None):
        result,form=svc.task_status(actor=actor or self.manager,task_id=obj.pk,data=dict(revision=obj.updated_at.isoformat(),reason='SYNTHETIC status check',status=status))
        self.assertIsNotNone(result,form.errors);return result
    def note(self,actor=None,old=None,**changes):
        data=dict(content='SYNTHETIC preparation note',visibility='staff',reason='SYNTHETIC note check',revision=old.updated_at.isoformat() if old else 'new');data.update(changes)
        obj,form=svc.save_note(actor=actor or self.manager,event_id=self.event.pk,data=data,note_id=old.pk if old else None)
        self.assertIsNotNone(obj,form.errors);return obj
    def document(self,demo=True,**changes):
        data=dict(title='SYNTHETIC instructions',description='SYNTHETIC uploaded PDF',visibility='staff',reason='SYNTHETIC storage check',confirm='on');data.update(changes)
        upload=SimpleUploadedFile('SYNTHETIC-instructions.pdf',storage.demo_pdf(),content_type='application/pdf') if not demo else None
        obj,form=svc.create_document(actor=self.manager,event_id=self.event.pk,data=data,upload=upload,demo=demo)
        self.assertIsNotNone(obj,form.errors);return obj
    def event_url(self,section):return reverse('operations:event',args=[self.event.pk,section])
    def test_standalone_task_creation(self):
        t=self.task(priority='high',due_date='2030-07-03');self.assertIsNone(t.event_id);self.assertEqual(t.priority,'high');self.assertEqual(t.due_date,date(2030,7,3));self.assertEqual(t.created_by,self.manager)
    def test_event_task_uses_same_record_in_both_lists(self):
        t=self.task(event=str(self.event.pk));self.assertContains(self.client.get('/operations/tasks/?view=all'),t.title);self.assertContains(self.client.get(self.event_url('tasks')),t.title);self.assertEqual(Task.objects.count(),1)
    def test_event_creation_form_preselects_and_locks_event(self):
        url=reverse('operations:event-task-add',args=[self.event.pk]);response=self.client.get(url);self.assertTrue(response.context['form'].fields['event'].disabled)
        data=dict(title='SYNTHETIC event-created task',description='',assigned_staff=str(self.person.pk),event='',priority='low',reason='SYNTHETIC create',revision='new')
        self.assertEqual(self.client.post(url,data).status_code,302);self.assertEqual(Task.objects.get().event_id,self.event.pk)
    def test_task_edit_reassignment_is_audited(self):
        old=self.task();new=self.task(old=old,title='SYNTHETIC changed',assigned_staff=str(self.other_person.pk));self.assertEqual(new.assigned_staff,self.other_person);self.assertTrue(AuditLog.objects.filter(action='operations.task.reassigned').exists())
    def test_inactive_staff_rejected_for_new_task(self):
        self.person.is_active=False;self.person.save();obj,form=svc.save_task(actor=self.manager,data=dict(title='SYNTHETIC',assigned_staff=str(self.person.pk),priority='normal',revision='new',reason='SYNTHETIC'));self.assertIsNone(obj)
    def test_staff_sees_only_own_tasks_and_no_staff_private_notes(self):
        own=self.task();self.task(assigned_staff=str(self.other_person.pk),title='OTHER CONFIDENTIAL TASK');self.login(self.employee)
        r=self.client.get('/operations/my-tasks/');self.assertContains(r,own.title);self.assertNotContains(r,'OTHER CONFIDENTIAL TASK');self.assertNotContains(r,'SYNTHETIC PRIVATE STAFF INFORMATION')
    def test_staff_cannot_open_other_task(self):
        t=self.task(assigned_staff=str(self.other_person.pk));self.login(self.employee);self.assertEqual(self.client.get(reverse('operations:task',args=[t.pk])).status_code,404)
    def test_staff_can_progress_and_complete_with_attribution(self):
        t=self.task();t=self.status(t,'in_progress',self.employee);t=self.status(t,'completed',self.employee);self.assertEqual(t.completed_by,self.employee);self.assertIsNotNone(t.completed_at);self.assertEqual(t.status,'completed')
    def test_assignee_can_cancel_and_reopen(self):
        t=self.task();t=self.status(t,'cancelled',self.employee);t=self.status(t,'completed',self.employee)
        t=self.status(t,'in_progress',self.employee);self.assertIsNone(t.completed_at)
    def test_staff_cannot_change_ownership_via_status_post(self):
        t=self.task();self.login(self.employee);r=self.client.post(reverse('operations:task-status',args=[t.pk]),dict(revision=t.updated_at.isoformat(),reason='SYNTHETIC',status='completed',assigned_staff=str(self.other_person.pk),title='ATTACK'))
        self.assertEqual(r.status_code,302);t.refresh_from_db();self.assertEqual(t.assigned_staff,self.person);self.assertNotEqual(t.title,'ATTACK')
    def test_staff_can_create_and_edit_assigned_tasks(self):
        t=self.task();self.login(self.employee)
        for url in ['/operations/tasks/create/',reverse('operations:task-edit',args=[t.pk])]:self.assertEqual(self.client.get(url).status_code,200)
    def test_completed_history_survives_manager_reopening(self):
        t=self.status(self.task());completed=t.completed_at;t=self.status(t,'todo');self.assertIsNone(t.completed_at);self.assertTrue(AuditLog.objects.filter(action='operations.task.completed',object_id=str(t.pk)).exists());self.assertIsNotNone(completed)
    def test_cancelled_history_is_not_deleted(self):
        t=self.status(self.task(),'cancelled');self.assertTrue(Task.objects.filter(pk=t.pk,status='cancelled').exists());self.assertIsNone(t.completed_at)
    def test_stale_task_edit_rejected(self):
        t=self.task();self.task(old=t,title='SYNTHETIC newer')
        with self.assertRaises(ValidationError):self.task(old=t,title='SYNTHETIC stale')
    def test_stale_task_status_rejected(self):
        t=self.task();self.status(t,'in_progress')
        with self.assertRaises(ValidationError):self.status(t)
    def test_due_date_validation(self):
        f=TaskForm(dict(title='SYNTHETIC',assigned_staff=str(self.person.pk),priority='normal',due_date='bad',reason='SYNTHETIC'));self.assertFalse(f.is_valid())
    def test_overdue_excludes_completed_and_cancelled(self):
        old=(timezone.localdate()-timedelta(days=1)).isoformat();a=self.task(due_date=old,title='SYNTHETIC overdue');self.status(self.task(due_date=old,title='DONE'));self.status(self.task(due_date=old,title='CANCELLED'),'cancelled')
        r=self.client.get('/operations/tasks/?view=overdue');self.assertContains(r,a.title);self.assertNotContains(r,'DONE');self.assertNotContains(r,'CANCELLED')
    def test_filters_combine_search_priority_status_staff_event(self):
        t=self.task(event=str(self.event.pk),priority='high');self.task(title='SYNTHETIC unrelated')
        r=self.client.get('/operations/tasks/',dict(view='all',q='prepare',priority='high',status='todo',assignee=str(self.person.pk),event=str(self.event.pk)));self.assertEqual(r.context['tasks'].paginator.count,1)
    def test_bad_filter_ids_do_not_error(self):self.assertEqual(self.client.get('/operations/tasks/?event=bad&assignee=bad').status_code,200)
    def test_completed_filter(self):
        t=self.status(self.task());self.assertContains(self.client.get('/operations/tasks/?view=completed'),t.title)
    def test_task_list_paginates(self):
        for i in range(23):self.task(title=f'SYNTHETIC {i}')
        r=self.client.get('/operations/tasks/?page=2&view=all');self.assertEqual(len(r.context['tasks']),3);self.assertContains(r,'Previous')
    def test_task_access_independent_of_draft_event_access(self):
        t=self.task(event=str(self.event.pk));self.event.status='draft';self.event.save();self.login(self.employee)
        self.assertContains(self.client.get('/operations/my-tasks/'),t.title)
        r=self.client.get(reverse('operations:task',args=[t.pk]));self.assertEqual(r.status_code,200)
        self.assertNotContains(r,self.event.name)
    def test_archived_event_task_lifecycle_preserves_relationship(self):
        t=self.task(event=str(self.event.pk));self.event.status='archived';self.event.save()
        t=self.status(t);self.assertEqual(t.event_id,self.event.pk)
    def test_note_creation_author_and_timestamps(self):
        n=self.note(actor=self.employee);self.assertEqual(n.author,self.employee);self.assertEqual(n.visibility,'staff');self.assertIsNotNone(n.created_at)
    def test_note_edit_preserves_author_and_audit_content(self):
        n=self.note(actor=self.employee);created=n.created_at;n=self.note(actor=self.employee,old=n,content='SYNTHETIC corrected');self.assertEqual(n.author,self.employee);self.assertEqual(n.edited_by,self.employee);self.assertEqual(n.created_at,created)
        a=AuditLog.objects.get(action='operations.note.edited');self.assertEqual(a.old_data['content'],'SYNTHETIC preparation note');self.assertEqual(a.new_data['content'],'SYNTHETIC corrected')
    def test_manager_only_notes_hidden_from_staff(self):
        self.note(visibility='managers',content='CONFIDENTIAL MANAGER NOTE');self.note();self.login(self.employee);r=self.client.get(self.event_url('notes'));self.assertNotContains(r,'CONFIDENTIAL MANAGER NOTE');self.assertContains(r,'SYNTHETIC preparation note');self.assertNotContains(r,'SYNTHETIC CONFIDENTIAL EVENT DESCRIPTION')
    def test_staff_cannot_edit_manager_note_even_own(self):
        n=self.note(visibility='managers');n.author=self.employee;n.save()
        with self.assertRaises(PermissionDenied):self.note(actor=self.employee,old=n)
    def test_staff_cannot_edit_other_authors_notes(self):
        n=self.note()
        with self.assertRaises(PermissionDenied):self.note(actor=self.employee,old=n)
    def test_unassigned_staff_cannot_read_or_add_event_notes(self):
        self.login(self.other);self.assertEqual(self.client.get(self.event_url('notes')).status_code,403);self.assertEqual(self.client.post(self.event_url('notes'),{}).status_code,403)
    def test_staff_cannot_create_manager_only_note(self):
        obj,form=svc.save_note(actor=self.employee,event_id=self.event.pk,data=dict(content='SYNTHETIC',visibility='managers',revision='new',reason='SYNTHETIC'));self.assertIsNone(obj)
    def test_notes_chronological_order(self):
        a=self.note(content='SYNTHETIC first');b=self.note(content='SYNTHETIC second');r=self.client.get(self.event_url('notes'));self.assertLess(r.content.index(b'SYNTHETIC first'),r.content.index(b'SYNTHETIC second'))
    def test_note_archive_preserves_content_and_hides_staff(self):
        n=self.note();saved,f=svc.archive(actor=self.manager,obj_id=n.pk,kind='event_notes',data=dict(revision=n.updated_at.isoformat(),reason='SYNTHETIC archive'));self.assertIsNotNone(saved);self.login(self.employee);self.assertNotContains(self.client.get(self.event_url('notes')),n.content);self.assertTrue(EventNote.objects.filter(pk=n.pk).exists())
    def test_stale_note_edit_rejected(self):
        n=self.note();self.note(old=n,content='SYNTHETIC newer')
        with self.assertRaises(ValidationError):self.note(old=n)
    def test_archived_event_notes_read_only(self):
        self.event.status='archived';self.event.save()
        with self.assertRaises(ValidationError):self.note()
    def test_demo_pdf_is_parseable_and_visibly_synthetic(self):
        from pypdf import PdfReader
        reader=PdfReader(BytesIO(storage.demo_pdf()),strict=True);self.assertIn('SYNTHETIC',reader.pages[0].extract_text())
    def test_demo_download_permission_and_headers(self):
        d=self.document();self.login(self.employee);r=self.client.get(reverse('operations:download',args=[d.pk]));self.assertEqual(r.status_code,200);self.assertEqual(r['Cache-Control'],'private, no-store');self.assertEqual(r['X-Content-Type-Options'],'nosniff');self.assertEqual(b''.join(r.streaming_content),storage.demo_pdf());self.assertIn('attachment',r['Content-Disposition'])
    def test_manager_only_document_denies_staff(self):
        d=self.document(visibility='managers');self.login(self.employee);self.assertEqual(self.client.get(reverse('operations:download',args=[d.pk])).status_code,403)
    def test_unassigned_document_access_denied(self):
        d=self.document();self.login(self.other);self.assertEqual(self.client.get(reverse('operations:download',args=[d.pk])).status_code,403)
    def test_staff_cannot_upload_demo_or_archive(self):
        d=self.document();self.login(self.employee)
        for url in [reverse('operations:upload',args=[self.event.pk]),reverse('operations:demo',args=[self.event.pk]),reverse('operations:archive',args=['event_documents',d.pk])]:self.assertEqual(self.client.post(url,{}).status_code,403)
    def test_archived_document_retained_but_denied_to_staff(self):
        d=self.document();saved,f=svc.archive(actor=self.manager,obj_id=d.pk,kind='event_documents',data=dict(revision=d.updated_at.isoformat(),reason='SYNTHETIC archive'));self.assertIsNotNone(saved);self.assertTrue(EventDocument.objects.filter(pk=d.pk).exists());self.login(self.employee);self.assertEqual(self.client.get(reverse('operations:download',args=[d.pk])).status_code,403)
    def test_missing_durable_storage_rejects_real_upload(self):
        with override_settings(OPERATIONAL_DOCUMENT_STORAGE_DURABLE=False):
            with self.assertRaises(ValidationError):self.document(demo=False)
            self.assertContains(self.client.get(self.event_url('documents')),'Real uploads unavailable')
    def test_real_private_upload_and_download_uses_separate_root(self):
        with tempfile.TemporaryDirectory() as root,override_settings(OPERATIONAL_DOCUMENT_ROOT=root,OPERATIONAL_DOCUMENT_STORAGE_DURABLE=True):
            d=self.document(demo=False);self.assertTrue(d.storage_key.startswith('operational-events/'));self.assertTrue((Path(root)/d.storage_key).is_file());self.assertNotIn(d.original_filename,d.storage_key)
            self.login(self.employee);r=self.client.get(reverse('operations:download',args=[d.pk]));self.assertEqual(b''.join(r.streaming_content),storage.demo_pdf())
    def test_storage_never_exposes_public_url(self):
        with tempfile.TemporaryDirectory() as root,override_settings(OPERATIONAL_DOCUMENT_ROOT=root):
            with self.assertRaises(ValueError):storage.OperationalDocumentStorage().url('secret.pdf')
    def test_missing_bytes_returns_404_and_retains_metadata(self):
        with tempfile.TemporaryDirectory() as root,override_settings(OPERATIONAL_DOCUMENT_ROOT=root,OPERATIONAL_DOCUMENT_STORAGE_DURABLE=True):
            d=self.document(demo=False);(Path(root)/d.storage_key).unlink();r=self.client.get(reverse('operations:download',args=[d.pk]));self.assertEqual(r.status_code,404);self.assertTrue(EventDocument.objects.filter(pk=d.pk).exists())
    def test_modified_bytes_rejected_by_integrity_check(self):
        d=self.document();EventDocument.objects.filter(pk=d.pk).update(sha256='0'*64);self.assertEqual(self.client.get(reverse('operations:download',args=[d.pk])).status_code,404)
    def test_invalid_extension_signature_and_malformed_pdf_rejected(self):
        for name,body in [('bad.exe',storage.demo_pdf()),('bad.pdf',b'<html>unsafe</html>'),('bad.pdf',b'%PDF-1.4 broken')]:
            with self.assertRaises(ValidationError):storage.validate_upload(SimpleUploadedFile(name,body))
    def test_size_limit_and_empty_upload(self):
        with override_settings(OPERATIONAL_DOCUMENT_MAX_UPLOAD_BYTES=10):
            for body in [b'',storage.demo_pdf()]:
                with self.assertRaises(ValidationError):storage.validate_upload(SimpleUploadedFile('test.pdf',body))
    def test_filename_windows_path_rejected(self):
        f=SimpleUploadedFile('safe.pdf',storage.demo_pdf());f.name='..'+chr(92)+'secret.pdf'
        with self.assertRaises(ValidationError):storage.validate_upload(f)
    def test_image_validation(self):
        from PIL import Image
        out=BytesIO();Image.new('RGB',(2,2)).save(out,format='PNG');name,mime,body=storage.validate_upload(SimpleUploadedFile('SYNTHETIC.png',out.getvalue()));self.assertEqual(mime,'image/png')
    def test_upload_audit_failure_cleans_up_bytes(self):
        with tempfile.TemporaryDirectory() as root,override_settings(OPERATIONAL_DOCUMENT_ROOT=root,OPERATIONAL_DOCUMENT_STORAGE_DURABLE=True):
            with patch('apps.operations.services.audit',side_effect=RuntimeError('SYNTHETIC audit failure')):
                with self.assertRaises(RuntimeError):self.document(demo=False)
            self.assertEqual(EventDocument.objects.count(),0);self.assertFalse(any(p.is_file() for p in Path(root).rglob('*')))
    @override_settings(DEBUG=False,BTW_PREVIEW_GATE_ENABLED=False)
    def test_synthetic_demo_disabled_outside_development_and_gated_preview(self):
        with self.assertRaises(ValidationError):self.document()
    def test_documents_do_not_create_participant_records(self):
        from apps.participants.participant_document_models import ParticipantDocument
        count=ParticipantDocument.objects.count();self.document();self.assertEqual(ParticipantDocument.objects.count(),count)
    def test_draft_event_operations_hidden_from_staff(self):
        self.event.status='draft';self.event.save();self.login(self.employee)
        for section in ['tasks','notes','documents']:self.assertEqual(self.client.get(self.event_url(section)).status_code,403)
    def test_staff_navigation_exposes_my_tasks_and_event_operations(self):
        self.login(self.employee);self.assertContains(self.client.get('/operations/tasks/'),'Tasks');self.assertContains(self.client.get('/internal/'),'Personal settings')
    def test_manager_event_detail_and_staffing_links(self):
        r=self.client.get(reverse('events:event_detail',args=[self.event.pk]));self.assertContains(r,self.event_url('tasks'));self.assertContains(r,self.event_url('notes'));self.assertContains(r,self.event_url('documents'))
    def test_operations_event_pagination(self):
        for i in range(22):Event.objects.create(name=f'SYNTHETIC event {i}',start_at=self.event.start_at,end_at=self.event.end_at)
        r=self.client.get('/operations/events/?page=2');self.assertEqual(len(r.context['rows']),3);self.assertContains(r,'Previous')
    def test_anonymous_public_inactive_denied(self):
        self.client.logout();self.assertEqual(self.client.get('/operations/tasks/').status_code,302);self.login(self.public);self.assertEqual(self.client.get('/operations/tasks/').status_code,403)
        self.employee.is_active=False;self.employee.save();self.login(self.employee);self.assertNotEqual(self.client.get('/operations/tasks/').status_code,200)
    def test_csrf_required(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.manager,backend='django.contrib.auth.backends.ModelBackend');self.assertEqual(c.post('/operations/tasks/create/',{}).status_code,403)
    def test_all_manager_pages_render(self):
        t=self.task();n=self.note();d=self.document()
        urls=['/operations/tasks/','/operations/my-tasks/','/operations/tasks/create/','/operations/events/',reverse('operations:task',args=[t.pk]),reverse('operations:task-edit',args=[t.pk]),reverse('operations:task-status',args=[t.pk]),reverse('operations:note-edit',args=[n.pk]),reverse('operations:demo',args=[self.event.pk]),reverse('operations:archive',args=['event_notes',n.pk])]+[self.event_url(v) for v in ['tasks','notes','documents']]
        for url in urls:self.assertEqual(self.client.get(url).status_code,200,url)
    def test_operations_not_in_offline_export(self):
        from apps.events.downsync import EVENT_FIELDS
        for name in ['operational_tasks','operational_notes','operational_documents']:self.assertNotIn(name,EVENT_FIELDS)

    def test_task_admin_does_not_require_event_view_permission(self):
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label='events',codename='view_event'))
        actor=get_user_model().objects.get(pk=self.manager.pk)
        self.assertTrue(svc.task_access.task_admin(actor))
        self.assertIsNotNone(self.task())
    def test_staff_event_links_discover_operations_without_manager_event_access(self):
        self.login(self.employee);r=self.client.get(reverse('staffing:staff-event',args=[self.event.pk]));self.assertContains(r,self.event_url('notes'));self.assertNotContains(r,'SYNTHETIC CONFIDENTIAL EVENT DESCRIPTION')
    def test_task_view_navigation_is_available_on_mobile(self):
        self.login(self.employee);r=self.client.get('/operations/my-tasks/');self.assertContains(r,'aria-label="Task views"');self.assertNotContains(r,'class="event-detail-tabs"');self.assertEqual(r.content.count(b'<h1'),1)
    def test_note_content_is_escaped(self):
        self.note(content='<script>SYNTHETIC</script>');self.login(self.employee);r=self.client.get(self.event_url('notes'));self.assertContains(r,'&lt;script&gt;SYNTHETIC&lt;/script&gt;');self.assertNotContains(r,'<script>SYNTHETIC</script>')
