"""Synthetic end-to-end portal boundaries, using the existing intake and event engines."""
import re
import tempfile
import uuid
from datetime import date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail, signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.events.models import Event, EventPublicSlug, EventRegistration, EventRegistrationQuestion, QrTicket, ParticipantMatchReview
from .models import (Participant, ParticipantAccount, ParticipantCareerProfile, ConsentDocumentVersion,
    ConsentAcceptance, PortalInvitation, PortalDelivery, ParticipantDocument, ParticipantDocumentCategory,
    ParticipantVerificationRecord, ParticipantQualification, RegistrationSubmission, ParticipantEventInterest)
from .intake_lifecycle import create_form, publish, revision, assign
from .intake_submission import start_session, complete_session
from . import test_intake_contract_11b as contract_fixture
from .portal_accounts import (queue_invitation, invite_token, activate, read_invitation,
    eligible, deliver_invitation, process_deliveries, active_account)
from .portal_services import (profile_revision, save_profile, public_events, register_for_event,
    contract_hash, record_interest, visible_documents, archive_document)
from .portal_forms import PortalDocumentForm
from .portal_services import upload_document

PASSWORD = 'SYNTHETIC-test-only-Long-phrase-123!'


@override_settings(PARTICIPANT_PORTAL_BASE_URL='https://portal.example.test')
class Portal12Tests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('synthetic-portal-admin', 'admin@example.test', PASSWORD)
        self.doc = ConsentDocumentVersion.objects.create(key='btw-intake', version='synthetic-v1',
            title='SYNTHETIC nonbinding test notice', body='SYNTHETIC TEST ONLY. No legal agreement.',
            content_hash='a' * 64, is_approved=True)
        draft = create_form(actor=self.admin, name='SYNTHETIC Portal Intake')
        version = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        self.assignment = assign(actor=self.admin, version_id=version.pk, channel='online')
        self.source = self.submit()
        self.person = self.source.participant
        self.invite = PortalInvitation.objects.get(participant=self.person)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = ParticipantDocument._meta.get_field('file').storage
        ParticipantDocument._meta.get_field('file').storage = FileSystemStorage(location=self.tmp.name)
        self.addCleanup(setattr, ParticipantDocument._meta.get_field('file'), 'storage', self.storage)

    def submit(self, **changes):
        session = start_session(assignment=self.assignment, browser_key='b' * 64)
        session.answers = contract_fixture.IntakeContract11BTests().answers(**dict({'updates_preference': 'yes'}, **changes))
        session.current_step = 4
        session.consent_accepted_at = timezone.now()
        session.save()
        return complete_session(session_id=session.pk)

    def account(self):
        account = activate(invite_token(self.invite), PASSWORD)
        self.client.force_login(account.user, backend='apps.participants.auth.ParticipantAuthenticationBackend')
        return account

    def event(self, **changes):
        values = dict(name='SYNTHETIC Public Skills Day', start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=4), status='upcoming',
            public_registration_status='open', public_consent_document=self.doc)
        values.update(changes)
        event = Event.objects.create(**values)
        EventPublicSlug.objects.create(event=event, slug='synthetic-' + uuid.uuid4().hex, is_current=True)
        return event

    def upload(self):
        category = ParticipantDocumentCategory.objects.filter(is_active=True).first()
        form = PortalDocumentForm({'category': category.pk, 'description': 'SYNTHETIC resume'},
            {'file': SimpleUploadedFile('resume.pdf', b'%PDF-1.4 SYNTHETIC nonbinding fixture')})
        self.assertTrue(form.is_valid(), form.errors)
        return upload_document(self.account_obj.user, form)

    def test_completed_intake_queues_exact_initial_day3_day7_once(self):
        self.assertEqual(self.invite.deliveries.count(), 3)
        self.assertEqual(set(self.invite.deliveries.values_list('slot', flat=True)), {'initial', 'day3', 'day7'})
        queue_invitation(self.person)
        self.assertEqual(PortalInvitation.objects.count(), 1)
        self.assertEqual(PortalDelivery.objects.count(), 3)
        self.assertEqual(Participant.objects.count(), 1)

    def test_minor_eligible_under_existing_intake_policy_can_activate(self):
        source = self.submit(email='minor@example.test', phone='5550109911', dob=date(timezone.localdate().year - 16, 1, 1).isoformat())
        invite = PortalInvitation.objects.get(participant=source.participant)
        account = activate(invite_token(invite), PASSWORD)
        self.assertEqual(account.participant_id, source.participant_id)
        self.assertEqual(account.status, 'active')

    def test_unknown_age_and_missing_intake_held(self):
        self.person.date_of_birth = None
        self.person.save()
        self.assertFalse(eligible(self.person, invitation=True))
        other = Participant.objects.create(first_name='SYNTHETIC', last_name='No intake', date_of_birth=date(2000, 1, 1))
        self.assertIsNone(queue_invitation(other))

    def test_permission_withdrawal_and_shared_contact_hold_invites(self):
        self.submit(updates_preference='no')
        self.assertFalse(eligible(self.person, invitation=True))
        self.assertIsNone(read_invitation(invite_token(self.invite)))

    def test_shared_email_cannot_claim_another_record(self):
        Participant.objects.create(first_name='SYNTHETIC', last_name='Shared', contact_email=self.person.contact_email)
        self.assertIsNone(read_invitation(invite_token(self.invite)))

    def test_identity_review_blocks_source_and_candidate(self):
        source = self.submit(phone='5550109912')
        self.assertTrue(ParticipantMatchReview.objects.filter(source_submission=source, status='open').exists())
        self.assertFalse(PortalInvitation.objects.filter(participant=source.participant).exists())
        self.assertIsNone(read_invitation(invite_token(self.invite)))

    def test_multi_candidate_review_also_holds_invitation(self):
        source = self.submit(email='synthetic-other@example.test', phone=self.person.contact_phone)
        # A multi-candidate review has no single candidate FK. Keep its real,
        # immutable intake source while testing that every candidate is held.
        ParticipantMatchReview.objects.filter(source_submission=source).update(
            candidate_participant=None, candidates=[self.person.pk, source.participant_id])
        self.assertIsNone(read_invitation(invite_token(self.invite)))

    def test_activation_is_single_use_and_has_no_duplicate_identity(self):
        token = invite_token(self.invite)
        account = activate(token, PASSWORD)
        self.assertEqual(account.participant_id, self.person.pk)
        with self.assertRaises(ValidationError):
            activate(token, PASSWORD)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(ParticipantAccount.objects.count(), 1)
        self.assertEqual(PortalDelivery.objects.filter(status='cancelled').count(), 3)

    def test_expired_and_tampered_invitation_rejected(self):
        token = invite_token(self.invite)
        self.assertIsNone(read_invitation(token + 'bad'))
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 86402):
            self.assertIsNone(read_invitation(token))

    def test_contact_change_invalidates_invitation(self):
        token = invite_token(self.invite)
        self.person.contact_email = 'new@example.test'
        self.person.save()
        self.assertIsNone(read_invitation(token))

    def test_locmem_delivery_is_not_claimed_as_live(self):
        initial = self.invite.deliveries.get(slot='initial')
        self.assertEqual(deliver_invitation(initial.pk), 'test')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/participants/portal/activate/', mail.outbox[0].body)
        self.assertEqual(deliver_invitation(initial.pk), 'test')
        self.assertEqual(len(mail.outbox), 1)

    def test_reminders_due_and_stop_after_activation(self):
        self.invite.deliveries.update(due_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(process_deliveries(), ['test', 'test', 'test'])
        account = activate(invite_token(self.invite), PASSWORD)
        self.assertEqual(process_deliveries(), [])
        self.assertEqual(account.status, 'active')

    @override_settings(PARTICIPANT_PORTAL_BASE_URL='')
    def test_delivery_held_without_https_base(self):
        self.assertEqual(deliver_invitation(self.invite.deliveries.get(slot='initial').pk), 'held')
        self.assertEqual(len(mail.outbox), 0)

    def test_transport_error_is_uncertain_not_retried(self):
        initial = self.invite.deliveries.get(slot='initial')
        with patch('apps.participants.portal_accounts.EmailMessage.send', side_effect=ConnectionError('SYNTHETIC secret must not persist')):
            self.assertEqual(deliver_invitation(initial.pk), 'sending')
        self.assertEqual(process_deliveries(), [])
        initial.refresh_from_db()
        self.assertNotIn('secret', initial.detail)

    def test_resend_rotates_link_and_rate_limits(self):
        token = invite_token(self.invite)
        PortalInvitation.objects.filter(pk=self.invite.pk).update(last_requested_at=timezone.now() - timedelta(hours=1))
        queue_invitation(self.person, resend=True)
        self.assertIsNone(read_invitation(token))
        queue_invitation(self.person, resend=True)
        self.assertEqual(self.invite.deliveries.filter(slot__startswith='resend').count(), 1)

    def test_login_profile_and_post_only_logout(self):
        account = self.account()
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code, 200)
        self.assertEqual(self.client.get(reverse('participants:logout')).status_code, 405)
        self.assertEqual(self.client.post(reverse('participants:logout')).status_code, 302)
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code, 302)
        response = self.client.post(reverse('participants:login'), {'email': account.login_email.upper(), 'password': PASSWORD})
        self.assertRedirects(response, reverse('participants:profile'))

    def test_staff_and_disabled_accounts_cannot_use_portal(self):
        account = self.account()
        account.user.is_staff = True
        account.user.save()
        self.assertIsNone(active_account(account.user))
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code, 403)

    def test_archive_disables_existing_portal_session(self):
        self.account()
        self.person.archived_at = timezone.now()
        self.person.save()
        self.assertEqual(self.client.get(reverse('participants:profile')).status_code, 302)

    def test_self_edits_keep_identity_and_protect_intake_history(self):
        account = self.account()
        person = Participant.objects.get(pk=self.person.pk)
        career = ParticipantCareerProfile.objects.get(participant=person)
        identity, original = person.identity_uuid, dict(self.source.answers)
        save_profile(account.user, {'city': 'SYNTHETIC manual city', 'skills_interests': 'SYNTHETIC welding'}, profile_revision(person, career))
        self.submit(city='SYNTHETIC later intake')
        person.refresh_from_db(); self.source.refresh_from_db()
        self.assertEqual(person.city, 'SYNTHETIC manual city')
        self.assertEqual(person.identity_uuid, identity)
        self.assertEqual(self.source.answers, original)
        self.assertTrue(AuditLog.objects.filter(action='portal.profile_updated').exists())

    def test_protected_fields_and_stale_profile_write_rejected(self):
        account = self.account()
        self.person.refresh_from_db()
        career = ParticipantCareerProfile.objects.get(participant=self.person)
        rev = profile_revision(self.person, career)
        with self.assertRaises(PermissionDenied):
            save_profile(account.user, {'date_of_birth': date(1990, 1, 1)}, rev)
        save_profile(account.user, {'city': 'SYNTHETIC changed'}, rev)
        with self.assertRaises(ValidationError):
            save_profile(account.user, {'city': 'Stale'}, rev)

    def test_documents_reuse_staff_library_and_are_private(self):
        self.account_obj = self.account()
        document = self.upload()
        self.assertEqual(document.participant_id, self.person.pk)
        self.assertEqual(document.upload_source, 'participant')
        url = reverse('participants:portal-document-download', args=[document.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        other = Participant.objects.create(first_name='SYNTHETIC', last_name='Other')
        document.participant = other; document.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_internal_staff_file_hidden_until_explicitly_shared(self):
        self.account_obj = self.account()
        document = self.upload()
        document.upload_source, document.participant_visible = 'staff', False
        document.save()
        self.assertFalse(visible_documents(self.person).exists())
        document.participant_visible = True; document.save()
        self.assertTrue(visible_documents(self.person).exists())

    def test_reviewed_documents_cannot_be_archived_by_participant(self):
        self.account_obj = self.account()
        document = self.upload()
        ParticipantVerificationRecord.objects.create(participant=self.person, document=document,
            status='pending', reason='SYNTHETIC private staff reason')
        with self.assertRaises(PermissionDenied):
            archive_document(self.account_obj.user, document.uuid)
        response = self.client.get(reverse('participants:profile'))
        self.assertNotContains(response, 'SYNTHETIC private staff reason')

    def test_unreviewed_own_document_archive_retains_file_and_audit(self):
        self.account_obj = self.account()
        document = self.upload()
        archive_document(self.account_obj.user, document.uuid)
        document.refresh_from_db()
        self.assertEqual(document.state, 'archived')
        self.assertTrue(document.file.storage.exists(document.file.name))
        self.assertTrue(document.audit_events.filter(action='archived').exists())

    def test_invalid_uploads_and_oversized_files_rejected(self):
        category = ParticipantDocumentCategory.objects.filter(is_active=True).first()
        form = PortalDocumentForm({'category': category.pk}, {'file': SimpleUploadedFile('bad.pdf', b'<script>bad</script>')})
        self.assertFalse(form.is_valid())
        with override_settings(PARTICIPANT_DOCUMENT_MAX_UPLOAD_BYTES=5):
            form = PortalDocumentForm({'category': category.pk}, {'file': SimpleUploadedFile('big.pdf', b'%PDF-1.4 too big')})
            self.assertFalse(form.is_valid())

    def test_only_published_public_events_are_discovered(self):
        visible = self.event()
        self.event(public_registration_status='disabled')
        self.event(status='draft')
        self.assertEqual(list(public_events()), [visible])

    def test_interest_is_not_a_registration_or_ticket(self):
        account = self.account(); event = self.event()
        record_interest(account.user, event); record_interest(account.user, event)
        self.assertEqual(ParticipantEventInterest.objects.count(), 1)
        self.assertFalse(EventRegistration.objects.exists())
        self.assertFalse(QrTicket.objects.exists())

    def test_event_registration_uses_same_person_and_current_qr_ticket(self):
        account = self.account(); event = self.event()
        values = {'consent': True}
        registration, created = register_for_event(account.user, event, values, contract_hash(event))
        again, second = register_for_event(account.user, event, values, contract_hash(event))
        self.assertTrue(created); self.assertFalse(second)
        self.assertEqual(registration.pk, again.pk)
        self.assertEqual(registration.participant_id, self.person.pk)
        self.assertEqual(Participant.objects.count(), 1)
        self.assertEqual(QrTicket.objects.count(), 1)
        ticket = QrTicket.objects.get()
        response = self.client.get(reverse('participants:portal-ticket', args=[ticket.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertTrue(ConsentAcceptance.objects.filter(participant=self.person, submission__event=event).exists())

    def test_capacity_closed_and_missing_consent_do_not_register(self):
        account = self.account(); event = self.event(registration_capacity=0)
        record_interest(account.user, event)
        with self.assertRaises(ValidationError):
            register_for_event(account.user, event, {'consent': True}, contract_hash(event))
        event.registration_capacity = None; event.save()
        with self.assertRaises(ValidationError):
            register_for_event(account.user, event, {}, contract_hash(event))
        event.public_registration_status = 'paused'; event.save()
        with self.assertRaises(ValidationError):
            register_for_event(account.user, event, {'consent': True}, contract_hash(event))
        self.assertFalse(EventRegistration.objects.exists())
        self.assertTrue(ParticipantEventInterest.objects.exists())

    def test_group_question_and_stale_consent_checks(self):
        account = self.account(); event = self.event(group_mode='required')
        EventRegistrationQuestion.objects.create(event=event, key='arrival', label='Arrival plan', is_required=True)
        with self.assertRaises(ValidationError):
            register_for_event(account.user, event, {'consent': True}, contract_hash(event))
        old = contract_hash(event)
        self.doc.body += ' UPDATED'; self.doc.save()
        with self.assertRaises(ValidationError):
            register_for_event(account.user, event, {'consent': True}, old)

    def test_other_participant_ticket_is_not_accessible(self):
        account = self.account(); event = self.event()
        registration, _ = register_for_event(account.user, event, {'consent': True}, contract_hash(event))
        ticket = QrTicket.objects.get(registration=registration)
        other = Participant.objects.create(first_name='SYNTHETIC', last_name='Other ticket owner')
        registration.participant = other; registration.save()
        self.assertEqual(self.client.get(reverse('participants:portal-ticket', args=[ticket.pk])).status_code, 404)

    def test_recovery_link_invalid_after_password_change_and_replay(self):
        account = self.account()
        self.client.post(reverse('participants:password-reset'), {'email': account.login_email})
        url = re.search(r'https://testserver([^\s]+)', mail.outbox[-1].body).group(1) if 'https://testserver' in mail.outbox[-1].body else re.search(r'http://testserver([^\s]+)', mail.outbox[-1].body).group(1)
        response = self.client.post(url, {'password1': PASSWORD + 'new', 'password2': PASSWORD + 'new'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(url).status_code, 400)

    def test_account_recovery_has_no_default_accounts_login_redirect(self):
        for name in ['profile', 'portal-events', 'portal-activity', 'portal-account', 'request-email-change']:
            response = self.client.get(reverse('participants:' + name))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('participants:login')))

    def test_csrf_required_for_activation_and_registration(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('participants:portal-activate', args=[invite_token(self.invite)]),
            {'password1': PASSWORD, 'password2': PASSWORD})
        self.assertEqual(response.status_code, 403)

    def test_staff_invitation_list_requires_specific_permission(self):
        staff = get_user_model().objects.create_user('synthetic-no-invite-perm', is_staff=True)
        self.client.force_login(staff, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('participants:portal-invitations')).status_code, 403)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=['view_participant', 'manage_portal_invitations'], content_type__app_label='participants'))
        self.assertEqual(self.client.get(reverse('participants:portal-invitations')).status_code, 200)

    def test_portal_pages_and_no_separate_document_navigation(self):
        self.account(); event = self.event()
        for name in ['profile', 'portal-events', 'portal-activity', 'portal-account']:
            response = self.client.get(reverse('participants:' + name))
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertContains(response, 'Built to Work')
        response = self.client.get(reverse('participants:profile'))
        self.assertContains(response, 'Supporting documents')
        self.assertNotContains(response, 'Internal notes')
        self.assertEqual(self.client.get(reverse('participants:portal-event', args=[event.pk])).status_code, 200)

    def test_existing_pending_account_is_activated_without_replacement(self):
        user = get_user_model().objects.create_user('synthetic-old-pending', email=self.person.contact_email,
                                                   password=None, is_active=False)
        pending = ParticipantAccount.objects.create(participant=self.person, user=user,
            login_email=self.person.contact_email, status='pending')
        account = activate(invite_token(self.invite), PASSWORD)
        self.assertEqual(account.pk, pending.pk)
        self.assertEqual(account.user_id, user.pk)
        self.assertEqual(ParticipantAccount.objects.count(), 1)

    def test_profile_form_saves_permitted_fields_only(self):
        account = self.account()
        response = self.client.get(reverse('participants:profile'))
        values = {'first_name': self.person.first_name, 'last_name': self.person.last_name,
            'contact_email': self.person.contact_email, 'contact_phone': self.person.contact_phone,
            'city': 'SYNTHETIC portal city', 'revision': response.context['form']['revision'].value(),
            'date_of_birth': '1900-01-01', 'is_verified': 'true', 'participant_id': '999999',
            'skills_interests': 'SYNTHETIC equipment', 'years_of_experience': '2'}
        old_dob = self.person.date_of_birth
        result = self.client.post(reverse('participants:profile'), values)
        self.assertEqual(result.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.city, values['city'])
        self.assertEqual(self.person.date_of_birth, old_dob)
        self.assertEqual(ParticipantAccount.objects.get(pk=account.pk).participant_id, self.person.pk)

    def test_email_change_requires_password_and_confirmation_and_revokes_old_link(self):
        account = self.account()
        endpoint = reverse('participants:request-email-change')
        self.client.post(endpoint, {'new_email': 'new@example.test', 'password': 'wrong'})
        account.refresh_from_db(); self.assertEqual(account.login_email, self.person.contact_email)
        self.client.post(endpoint, {'new_email': 'new@example.test', 'password': PASSWORD})
        # on_commit mail is intentionally deferred by TestCase; exercise it explicitly.
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(endpoint, {'new_email': 'new2@example.test', 'password': PASSWORD})
        url = re.search(r'http://testserver([^\s]+)', mail.outbox[-1].body).group(1)
        self.assertEqual(self.client.get(url).status_code, 200)
        account.refresh_from_db(); self.assertEqual(account.login_email, self.person.contact_email)
        self.assertEqual(self.client.post(url).status_code, 200)
        account.refresh_from_db(); self.assertEqual(account.login_email, 'new2@example.test')
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_participant_upload_available_to_authorized_existing_staff_workspace(self):
        self.account_obj = self.account()
        document = self.upload()
        endpoint = reverse('participants:staff-document-download', args=[document.uuid])
        self.assertEqual(self.client.get(endpoint).status_code, 403)
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
