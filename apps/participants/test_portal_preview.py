"""Synthetic-only admin preview boundaries and existing participant regressions."""
import base64
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.db import connection, close_old_connections
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event, EventRegistration, QrTicket, TicketAllocationBlock
from apps.events.services import register_participant
from .models import (Participant, ParticipantAccount, ParticipantDocument, ParticipantDocumentCategory,
    PortalPreviewFixture, PortalInvitation)
from .portal_accounts import queue_invitation, active_account
from .portal_preview import SESSION_KEY, fixture_for, participant_context
from .portal_services import public_events, save_profile, profile_revision

GATE = 'Basic ' + base64.b64encode(b'preview-reviewer:synthetic-test-only').decode()


@override_settings(BTW_PREVIEW_GATE_ENABLED=True, BTW_PREVIEW_GATE_USERNAME='preview-reviewer',
    BTW_PREVIEW_GATE_PASSWORD='synthetic-test-only', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PortalPreviewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser('synthetic-preview-admin','preview-admin@example.test','synthetic-unit-password')
        self.staff = User.objects.create_user('synthetic-preview-staff',is_staff=True)
        self.other_admin = User.objects.create_superuser('synthetic-other-admin','other-admin@example.test','synthetic-unit-password')
        self.person = Participant.objects.create(first_name='SYNTHETIC',last_name='Other person')
        self.participant_user = User.objects.create_user('synthetic-real-portal')
        ParticipantAccount.objects.create(participant=self.person,user=self.participant_user,
            login_email='synthetic-portal@example.test',status='active',email_verified_at=timezone.now())
        self.real_event = Event.objects.create(name='SYNTHETIC non-preview event',start_at=timezone.now()+timedelta(days=3),
            end_at=timezone.now()+timedelta(days=3,hours=4),status='upcoming')
        self.client.defaults['HTTP_AUTHORIZATION'] = GATE
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        field = ParticipantDocument._meta.get_field('file'); old_storage = field.storage
        field.storage = FileSystemStorage(location=self.tmp.name)
        self.addCleanup(setattr,field,'storage',old_storage)

    def enter(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.post(reverse('internal:portal-preview-enter'))
        self.assertRedirects(response,reverse('participants:profile'))
        return PortalPreviewFixture.objects.get(administrator=self.admin)

    def test_admin_entry_exit_preserves_authenticated_identity_and_password(self):
        original = self.admin.password
        fixture = self.enter()
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
        self.assertEqual(self.client.session['_auth_user_backend'],'django.contrib.auth.backends.ModelBackend')
        self.assertFalse(ParticipantAccount.objects.filter(participant=fixture.participant).exists())
        self.assertContains(self.client.get(reverse('participants:profile')),'Admin Preview — Participant Portal')
        self.assertRedirects(self.client.post(reverse('internal:portal-preview-exit')),reverse('internal:portal-preview'))
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))
        self.assertNotIn(SESSION_KEY,self.client.session)
        self.admin.refresh_from_db();self.assertEqual(self.admin.password,original)
        self.assertEqual(self.client.get('/internal/').status_code,200)
        self.assertTrue(AuditLog.objects.filter(action='portal.preview_entered',actor=self.admin).exists())
        self.assertTrue(AuditLog.objects.filter(action='portal.preview_exited',actor=self.admin).exists())

    def test_reopening_reuses_fixture(self):
        first = self.enter(); counts = (Participant.objects.count(),Event.objects.count())
        self.client.post(reverse('internal:portal-preview-exit'))
        self.client.post(reverse('internal:portal-preview-enter'))
        self.assertEqual(PortalPreviewFixture.objects.count(),1)
        self.assertEqual(PortalPreviewFixture.objects.get().pk,first.pk)
        self.assertEqual((Participant.objects.count(),Event.objects.count()),counts)

    def test_staff_and_participants_cannot_open_or_enter_admin_preview(self):
        for user,backend in [(self.staff,'django.contrib.auth.backends.ModelBackend'),
                (self.participant_user,'apps.participants.auth.ParticipantAuthenticationBackend')]:
            self.client.force_login(user,backend=backend)
            self.assertEqual(self.client.get(reverse('internal:portal-preview')).status_code,403)
            self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code,403)
        self.assertEqual(PortalPreviewFixture.objects.count(),0)

    def test_plain_admin_cannot_access_participant_data_without_preview(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code,403)
        self.assertIsNone(active_account(self.admin))

    @override_settings(BTW_PREVIEW_GATE_ENABLED=False)
    def test_preview_disabled_does_not_seed_records(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        before=(Participant.objects.count(),Event.objects.count())
        self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code,403)
        self.assertEqual(before,(Participant.objects.count(),Event.objects.count()))

    def test_forged_other_administrator_fixture_is_denied(self):
        fixture=self.enter()
        self.client.force_login(self.other_admin,backend='django.contrib.auth.backends.ModelBackend')
        session=self.client.session
        session[SESSION_KEY]={'fixture':str(fixture.pk),'administrator':str(self.other_admin.pk),'started':timezone.now().timestamp()}
        session.save()
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code,403)

    def test_malformed_preview_selection_is_denied(self):
        self.enter();session=self.client.session
        session[SESSION_KEY]={'fixture':'invalid-uuid','administrator':str(self.admin.pk),'started':timezone.now().timestamp()}
        session.save()
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code,403)

    def test_another_render_service_cannot_seed_preview_even_with_gate_enabled(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        with patch.dict('os.environ',{'RENDER_SERVICE_ID':'srv-synthetic-production'}):
            self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code,403)
        self.assertEqual(PortalPreviewFixture.objects.count(),0)

    def test_non_render_production_settings_cannot_seed_preview(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        with patch.dict('os.environ',{'DJANGO_SETTINGS_MODULE':'config.settings.production'}):
            self.assertEqual(self.client.post(reverse('internal:portal-preview-enter')).status_code,403)
        self.assertEqual(PortalPreviewFixture.objects.count(),0)

    def test_expired_preview_can_exit_without_logging_out_admin(self):
        self.enter();session=self.client.session; selected=session[SESSION_KEY]
        selected['started']-=3601;session[SESSION_KEY]=selected;session.save()
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code,403)
        self.assertEqual(self.client.post(reverse('internal:portal-preview-exit')).status_code,302)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))

    def test_state_changes_require_post_and_csrf(self):
        client=Client(enforce_csrf_checks=True,HTTP_AUTHORIZATION=GATE)
        client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        client.get(reverse('internal:portal-preview'));token=client.cookies['csrftoken'].value
        self.assertEqual(client.get(reverse('internal:portal-preview-enter')).status_code,405)
        self.assertEqual(client.post(reverse('internal:portal-preview-enter')).status_code,403)
        self.assertEqual(client.post(reverse('internal:portal-preview-enter'),{'csrfmiddlewaretoken':token}).status_code,302)
        self.assertEqual(client.get(reverse('internal:portal-preview-exit')).status_code,405)
        self.assertEqual(client.post(reverse('internal:portal-preview-exit')).status_code,403)
        self.assertEqual(client.post(reverse('internal:portal-preview-exit'),{'csrfmiddlewaretoken':token}).status_code,302)

    def test_profile_edits_only_fixture_and_locks_synthetic_contact(self):
        fixture=self.enter();before=self.person.updated_at
        page=self.client.get(reverse('participants:profile'))
        response=self.client.post(reverse('participants:profile'),{'revision':page.context['form']['revision'].value(),
            'first_name':'Claimed real person','contact_email':'person@invalid.example',
            'participant':self.person.pk,'preferred_name':'SYNTHETIC Edited','city':'Example Town'})
        self.assertEqual(response.status_code,302)
        fixture.participant.refresh_from_db();self.person.refresh_from_db()
        self.assertEqual(fixture.participant.preferred_name,'SYNTHETIC Edited')
        self.assertEqual(fixture.participant.first_name,'SYNTHETIC')
        self.assertEqual(fixture.participant.contact_email,f'portal-preview-{self.admin.pk}@example.test')
        self.assertEqual(self.person.updated_at,before)
        request=SimpleNamespace(user=self.admin,session=self.client.session)
        with participant_context(request),self.assertRaises(ValidationError):
            save_profile(self.admin,{'contact_email':'real@invalid.example'},profile_revision(fixture.participant,fixture.participant.career_profile))

    def test_documents_are_owned_by_fixture_and_other_documents_denied(self):
        fixture=self.enter();category=ParticipantDocumentCategory.objects.filter(is_active=True).first()
        response=self.client.post(reverse('participants:portal-document-add'),{'category':category.pk,
            'file':SimpleUploadedFile('synthetic.pdf',b'%PDF-1.4 SYNTHETIC PREVIEW ONLY')})
        self.assertEqual(response.status_code,302)
        document=ParticipantDocument.objects.get(participant=fixture.participant)
        response=self.client.get(reverse('participants:portal-document-download',args=[document.uuid]))
        self.assertEqual(response.status_code,200);self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        # A second preview administrator cannot access this fixture's file.
        self.client.post(reverse('internal:portal-preview-exit'))
        self.client.force_login(self.other_admin,backend='django.contrib.auth.backends.ModelBackend')
        self.client.post(reverse('internal:portal-preview-enter'))
        self.assertEqual(self.client.get(reverse('participants:portal-document-download',args=[document.uuid])).status_code,404)

    def test_event_interest_registration_ticket_and_real_event_isolation(self):
        fixture=self.enter()
        allocations=list(TicketAllocationBlock.objects.order_by('name').values('name','next_number'))
        self.assertEqual(self.client.get(reverse('participants:portal-event',args=[self.real_event.pk])).status_code,404)
        path=reverse('participants:portal-event',args=[fixture.event_id])
        self.client.post(path,{'action':'interest'})
        self.assertFalse(EventRegistration.objects.filter(event=fixture.event).exists())
        page=self.client.get(path);values={'contract':page.context['form']['contract'].value(),
            'q_preview_goal':'SYNTHETIC welding','consent':'on'}
        with patch('apps.events.services.allocate_ticket_number',side_effect=AssertionError('Preview must not allocate operational numbers')):
            self.assertRedirects(self.client.post(path,values),reverse('participants:portal-activity'))
            self.client.post(path,values)
        registration=EventRegistration.objects.get(event=fixture.event)
        self.assertEqual(registration.participant_id,fixture.participant_id)
        ticket=QrTicket.objects.get(registration=registration)
        self.assertIsNone(ticket.ticket_number)
        self.assertEqual(list(TicketAllocationBlock.objects.order_by('name').values('name','next_number')),allocations)
        ticket_path=reverse('participants:portal-ticket',args=[ticket.pk])
        self.assertContains(self.client.get(ticket_path),'Admin Preview — Participant Portal')
        self.assertContains(self.client.get(ticket_path),'not valid for event admission')
        import qrcode
        original=qrcode.QRCode.add_data;payloads=[]
        def capture(instance,data,*args,**kwargs):
            payloads.append(data);return original(instance,data,*args,**kwargs)
        with patch.object(qrcode.QRCode,'add_data',capture):
            response=self.client.get(ticket_path+'?image=1')
        self.assertEqual(response['Content-Type'],'image/png')
        self.assertEqual(payloads,['btw-admin-preview:'+str(ticket.pk)])
        self.assertEqual(EventRegistration.objects.filter(event=self.real_event).count(),0)

    def test_registration_service_rejects_both_cross_boundary_directions(self):
        fixture=self.enter();request=SimpleNamespace(user=self.admin,session=self.client.session)
        with self.assertRaises(ValidationError):
            register_participant(event=fixture.event,participant=fixture.participant,actor=self.admin)
        with participant_context(request):
            for event,person in [(self.real_event,fixture.participant),(fixture.event,self.person)]:
                with self.assertRaises(ValidationError):
                    register_participant(event=event,participant=person,actor=self.admin)
        self.assertEqual(EventRegistration.objects.count(),0)

    def test_public_event_discovery_and_invitation_cannot_claim_fixture(self):
        fixture=self.enter()
        self.assertFalse(public_events().filter(pk=fixture.event_id).exists())
        self.assertIsNone(queue_invitation(fixture.participant))
        self.assertFalse(PortalInvitation.objects.filter(participant=fixture.participant).exists())

    def test_account_changes_and_communications_blocked_in_preview(self):
        with patch('django.core.mail.EmailMessage.send') as send:
            self.enter()
            for name,kwargs in [('participants:password-reset',{}),('participants:portal-recover',{}),
                    ('participants:request-email-change',{}),('participants:verify-email-change',{'token':'invalid'}),
                    ('participants:password-reset-confirm',{'token':'invalid'}),('participants:portal-activate',{'token':'invalid'})]:
                self.assertEqual(self.client.post(reverse(name,kwargs=kwargs),{'email':'real@invalid.example'}).status_code,403)
            self.assertFalse(send.called)
        self.assertEqual(PortalInvitation.objects.count(),0)

    def test_participant_logout_in_preview_returns_to_admin(self):
        self.enter();self.client.post(reverse('participants:logout'))
        self.assertNotIn(SESSION_KEY,self.client.session)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.admin.pk))

    def test_banner_present_on_all_preview_html_pages(self):
        fixture=self.enter()
        for name,args in [('participants:profile',[]),('participants:portal-events',[]),
                ('participants:portal-event',[fixture.event_id]),('participants:portal-activity',[]),
                ('participants:portal-account',[]),('participants:portal-document-add',[])]:
            response=self.client.get(reverse(name,args=args))
            self.assertContains(response,'Admin Preview — Participant Portal')
            self.assertContains(response,'Return to Admin')

    def test_activation_labels_keep_existing_validation(self):
        from .forms import SetPasswordForm
        form=SetPasswordForm()
        self.assertEqual(form.fields['password1'].label,'Create Password')
        self.assertEqual(form.fields['password2'].label,'Confirm Password')
        self.assertFalse(SetPasswordForm({'password1':'short','password2':'different'}).is_valid())


@skipUnless(connection.vendor == 'postgresql', 'Fixture creation race requires PostgreSQL')
@override_settings(BTW_PREVIEW_GATE_ENABLED=True)
class PortalPreviewConcurrencyTests(TransactionTestCase):
    def test_simultaneous_first_entry_reuses_one_complete_fixture(self):
        admin=get_user_model().objects.create_superuser('synthetic-preview-race','race@example.test','synthetic-unit-password')
        barrier=Barrier(2)
        def create():
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return fixture_for(admin).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(create) for _ in range(2)]
            results=[future.result(timeout=60) for future in futures]
        self.assertEqual(results[0],results[1])
        self.assertEqual(PortalPreviewFixture.objects.count(),1)
        self.assertEqual(Participant.objects.count(),1)
        self.assertEqual(Event.objects.count(),1)
        self.assertEqual(ParticipantAccount.objects.count(),0)
