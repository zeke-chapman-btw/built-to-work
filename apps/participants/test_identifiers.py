"""Synthetic identifier ownership, contract compatibility and staff-only presentation."""
from copy import deepcopy
from datetime import timedelta
from django.test import TestCase
from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.urls import reverse
from apps.core.models import AuditLog
from .models import Participant, IntakeFieldState, RegistrationFormVersion, IntakeFormAssignment
from .identifier_definitions import PrimaryIdentifier, IDENTIFIER_SCHEMA, mapped_identifier
from .identifier_services import save_identifier, revision as identifier_revision
from .identifier_contracts import identifier_contract, prepare_identifier_draft
from .identifier_dry_run import historical_dry_run
from .intake_contracts import validate_contract, contract_hash
from .intake_lifecycle import publish, assign, revision
from .intake_projection import project_intake
from .discovery import discover
from .identity import identity_context
from . import test_intake_submission_11b as source_tests

class ParticipantIdentifierTests(TestCase):
    setUp = source_tests.IntakeSubmission11BTests.setUp
    answers = source_tests.IntakeSubmission11BTests.answers
    session = source_tests.IntakeSubmission11BTests.session
    submit = source_tests.IntakeSubmission11BTests.submit

    def manual(self, person, value, **kwargs):
        person.refresh_from_db()
        return save_identifier(actor=self.admin, participant_id=person.pk, value=value,
            expected_revision=kwargs.pop('expected_revision', identifier_revision(person)),
            reason=kwargs.pop('reason', 'SYNTHETIC classification correction'), **kwargs)

    def v2(self):
        draft = prepare_identifier_draft(actor=self.admin, form_id=self.version.form_id, expected_revision=revision(self.version))
        self.version = publish(actor=self.admin, version_id=draft.pk, expected_revision=revision(draft))
        self.assignment = assign(actor=self.admin, version_id=self.version.pk, channel='online')

    def test_exact_five_values_and_database_constraint(self):
        self.assertEqual(PrimaryIdentifier.values, ['student','employee','teacher_instructor','job_seeker','other'])
        person = Participant.objects.create(first_name='SYNTHETIC', last_name='Unset')
        self.assertEqual(person.primary_identifier, '')
        self.assertFalse(Participant._meta.get_field('primary_identifier').editable)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participant.objects.filter(pk=person.pk).update(primary_identifier='unclassified')

    def test_manual_reason_audit_stale_and_clear_protection(self):
        source = self.submit(current_situation='college')
        person = source.participant; person.refresh_from_db()
        self.assertEqual(person.primary_identifier, 'student')
        stale = identifier_revision(person)
        self.manual(person, 'teacher_instructor')
        self.assertEqual(AuditLog.objects.filter(action='participants.identifier.changed').count(), 1)
        change = AuditLog.objects.get(action='participants.identifier.changed')
        self.assertEqual(change.old_data['value'], 'student')
        self.assertEqual(change.new_data['value'], 'teacher_instructor')
        with self.assertRaises(ValidationError): self.manual(person, 'employee', expected_revision=stale)
        self.manual(person, 'employee', reason=' ')  # reasons are optional
        self.manual(person, '')
        self.submit(current_situation='employed_other', current_industry='retail')
        person.refresh_from_db()
        self.assertEqual(person.primary_identifier, '')
        owner = IntakeFieldState.objects.get(participant=person, destination='primary_identifier')
        self.assertTrue(owner.is_protected)
        self.assertEqual(owner.provenance['origin'], 'staff_cleared')

    def test_manual_correction_is_not_overwritten(self):
        source = self.submit(current_situation='college')
        self.manual(source.participant, 'other')
        self.submit(current_situation='employed_other', current_industry='retail')
        source.participant.refresh_from_db()
        self.assertEqual(source.participant.primary_identifier, 'other')

    def test_legacy_ambiguous_values_are_never_inferred(self):
        for key in ['other', 'unemployed', 'retired']:
            self.assertEqual(mapped_identifier('btw-intake-11b-v1', key), '')
        source = self.submit(current_situation='unemployed')
        source.participant.refresh_from_db()
        self.assertEqual(source.participant.primary_identifier, '')
        self.assertEqual(source.answers['current_situation'], 'unemployed')

    def test_frozen_v1_and_explicit_compatible_draft_no_publication_or_assignment(self):
        before = deepcopy(self.version.snapshot); digest = self.version.content_hash
        assignment_id = self.assignment.active_version_id
        draft = prepare_identifier_draft(actor=self.admin, form_id=self.version.form_id, expected_revision=revision(self.version))
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(draft.snapshot['schema'], IDENTIFIER_SCHEMA)
        self.assertEqual(len(draft.snapshot['questions']), len(before['questions']))
        self.version.refresh_from_db(); self.assignment.refresh_from_db()
        self.assertEqual(self.version.snapshot, before)
        self.assertEqual(self.version.content_hash, digest)
        self.assertEqual(contract_hash(before), digest)
        self.assertEqual(self.assignment.active_version_id, assignment_id)
        self.assertEqual(self.version.status, 'published')
        old = next(q for q in before['questions'] if q['key']=='current_situation')
        new = next(q for q in draft.snapshot['questions'] if q['key']=='current_situation')
        self.assertEqual(new['options'][:-2], old['options'])
        for a,b in zip(before['questions'], draft.snapshot['questions']):
            self.assertEqual(a['step'], b['step']); self.assertEqual(a.get('rule'), b.get('rule'))
        invalid = deepcopy(draft.snapshot); invalid['schema'] = 'btw-intake-11b-v1'
        with self.assertRaises(ValidationError): validate_contract(invalid)

    def test_v2_explicit_new_categories_and_other_map_without_changing_raw_answers(self):
        self.v2()
        for number, situation in enumerate(['teacher_instructor','job_seeker','other']):
            with self.subTest(situation=situation):
                source = self.submit(current_situation=situation, email=f'synthetic-id-{number}@example.test', phone=f'5550170{number:03}')
                source.participant.refresh_from_db()
                self.assertEqual(source.participant.primary_identifier, situation)
                self.assertEqual(source.answers['current_situation'], situation)
                self.assertEqual(source.intake_metadata['schema'], IDENTIFIER_SCHEMA)

    def test_pinned_v1_session_survives_v2_draft(self):
        session = self.session(current_situation='college')
        prepare_identifier_draft(actor=self.admin, form_id=self.version.form_id, expected_revision=revision(self.version))
        from .intake_submission import complete_session
        source = complete_session(session_id=session.pk)
        self.assertEqual(source.form_version_id, self.version.pk)
        source.participant.refresh_from_db()
        self.assertEqual(source.participant.primary_identifier, 'student')

    def test_replay_does_not_backfill_historical_projection(self):
        source = self.submit(current_situation='college')
        IntakeFieldState.objects.filter(participant=source.participant, destination='primary_identifier').delete()
        Participant.objects.filter(pk=source.participant_id).update(primary_identifier='')
        before = AuditLog.objects.filter(action='participants.identifier.projected').count()
        project_intake(submission=source)
        source.participant.refresh_from_db()
        self.assertEqual(source.participant.primary_identifier, '')
        self.assertEqual(AuditLog.objects.filter(action='participants.identifier.projected').count(), before)
        self.assertFalse(IntakeFieldState.objects.filter(participant=source.participant, destination='primary_identifier').exists())

    def test_delayed_first_projection_cannot_replace_newer_unmapped_answer(self):
        # Use genuine first projections while controlling their receipt order.
        source = self.submit(current_situation='college')
        person = source.participant
        from .identifier_services import project_identifier
        from .models import RegistrationSubmission
        from django.utils import timezone
        import uuid
        newer = RegistrationSubmission.objects.create(form_version=self.version, participant=person,
            status='submitted', intake_operation_id=uuid.uuid4(), submitted_at=timezone.now(),
            answers={'current_situation':'unemployed'}, intake_metadata={'answer_states': {'current_situation':'answered'}})
        with transaction.atomic():
            locked = Participant.objects.select_for_update().get(pk=person.pk)
            self.assertEqual(project_identifier(locked,newer), 'unmapped')
        older = RegistrationSubmission.objects.create(form_version=self.version, participant=person,
            status='submitted', intake_operation_id=uuid.uuid4(), submitted_at=source.submitted_at-timedelta(days=1),
            answers={'current_situation':'employed_other'}, intake_metadata={'answer_states': {'current_situation':'answered'}})
        with transaction.atomic():
            locked = Participant.objects.select_for_update().get(pk=person.pk)
            self.assertEqual(project_identifier(locked,older), 'older_or_replay')
        person.refresh_from_db(); self.assertEqual(person.primary_identifier, 'student')

    def test_directory_filter_unclassified_and_saved_criteria(self):
        source = self.submit(current_situation='college')
        unset = Participant.objects.create(first_name='SYNTHETIC', last_name='Unknown')
        with identity_context():
            form, people, criteria = discover(self.admin, {'identifier':'student'})
            self.assertTrue(form.is_valid()); self.assertEqual(list(people.values_list('pk',flat=True)), [source.participant_id])
            self.assertEqual(criteria['identifier'], 'student')
            self.assertEqual(list(discover(self.admin, {'identifier':'unclassified'})[1].values_list('pk',flat=True)),[unset.pk])
            self.assertFalse(discover(self.admin, {'identifier':'career_changer'})[0].is_valid())
            self.assertEqual(discover(self.admin, criteria, strict=True)[1].count(), 1)

    def test_editor_badge_reason_clear_and_read_only_boundary(self):
        source = self.submit(current_situation='college'); self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        url=reverse('participants:identifier-edit',args=[source.participant_id])
        profile=reverse('participants:staff-profile',args=[source.participant_id])
        self.assertContains(self.client.get(profile),'participant-identifier-badge')
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        form=response.context['form']
        self.assertEqual(len(form.fields['identifier'].choices),6) # five values and non-category prompt
        response=self.client.post(url,{'identifier':'','operation':'clear','revision':form.initial['revision'],'audit_reason':'SYNTHETIC cleared'},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,200)
        source.participant.refresh_from_db();self.assertEqual(source.participant.primary_identifier,'')
        user=get_user_model().objects.create_user('synthetic-id-reader',is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='view_participant',content_type__app_label='participants'))
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertNotContains(self.client.get(profile),'Edit identifier')
        with self.assertRaises(PermissionDenied):
            save_identifier(actor=user,participant_id=source.participant_id,value='other',expected_revision='',reason='SYNTHETIC')

    def test_dry_run_is_read_only_and_reports_ambiguity(self):
        source=self.submit(current_situation='college')
        self.submit(current_situation='unemployed')
        before=(Participant.objects.count(),AuditLog.objects.count(),IntakeFieldState.objects.count(),list(Participant.objects.values_list('pk','primary_identifier')))
        result=historical_dry_run()
        self.assertEqual(result['writes'],0);self.assertEqual(result['mode'],'dry_run_only')
        self.assertGreaterEqual(result['ambiguous_people'],1)
        self.assertEqual(before,(Participant.objects.count(),AuditLog.objects.count(),IntakeFieldState.objects.count(),list(Participant.objects.values_list('pk','primary_identifier'))))

    def test_v2_portable_package_does_not_activate_or_replace_old_assignment(self):
        from .intake_trailer import portable_version, install_version
        from .models import TrailerIntakeActivation
        from apps.events.models import Event, TrailerInstance, OfflineEventPreparation
        from django.utils import timezone
        import uuid
        original = portable_version(self.version)
        old_assignment = self.assignment.active_version_id
        self.v2()
        package = portable_version(self.version)
        self.assertEqual(package['contract']['schema'], IDENTIFIER_SCHEMA)
        package['stable_id'] = str(uuid.uuid4()); package['form_slug'] = 'synthetic-new-local-identifiers'; package['version'] = 1
        trailer = TrailerInstance.objects.create(identity='SYNTHETIC-ID-OFFLINE',display_name='Synthetic offline')
        event = Event.objects.create(name='SYNTHETIC identifier offline', start_at=timezone.now(),end_at=timezone.now()+timedelta(hours=3))
        prep = OfflineEventPreparation.objects.create(trailer=trailer,event=event)
        before = (IntakeFormAssignment.objects.count(),TrailerIntakeActivation.objects.count())
        installed = install_version(actor=self.admin, preparation=prep, package=package, confirmed=True, activate=False)
        self.assertEqual(installed.snapshot, package['contract'])
        self.assertEqual(installed.content_hash,package['content_hash'])
        self.assertEqual(before,(IntakeFormAssignment.objects.count(),TrailerIntakeActivation.objects.count()))
        old = RegistrationFormVersion.objects.get(pk=old_assignment)
        self.assertEqual(portable_version(old),original)

    def test_reviewed_identity_link_does_not_hide_conflicting_identifier_or_clear(self):
        from types import SimpleNamespace
        from .identifier_services import preserve_reviewed_links
        source=Participant.objects.create(first_name='SYNTHETIC',last_name='Source')
        target=Participant.objects.create(first_name='SYNTHETIC',last_name='Target')
        self.manual(source,'student');self.manual(target,'employee')
        source.refresh_from_db();target.refresh_from_db()
        link=SimpleNamespace(source_participant=source,candidate_participant=target)
        with transaction.atomic(), self.assertRaises(ValidationError): preserve_reviewed_links([link])
        self.manual(source,'');source.refresh_from_db()
        with transaction.atomic(), self.assertRaises(ValidationError): preserve_reviewed_links([link])
        self.manual(target,'');target.refresh_from_db()
        with transaction.atomic(): preserve_reviewed_links([link])
        self.assertEqual(AuditLog.objects.filter(action='participants.identifier.changed').count(),4)

    def test_active_unified_directory_renders_identifier_filter_on_both_devices(self):
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('participants:staff-directory'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'id="explorer-unified-identifier"',count=1)
        self.assertNotContains(response,'explorer-desktop-identifier')
        self.assertNotContains(response,'explorer-mobile-identifier')
        self.assertContains(response,'Unclassified')

    def test_dry_run_reports_reliable_student_employee_without_assigning_them(self):
        student=self.submit(current_situation='college',email='synthetic-history-student@example.test',phone='5550180001')
        employee=self.submit(current_situation='employed_other',current_industry='retail',email='synthetic-history-employee@example.test',phone='5550180002')
        ids=[student.participant_id,employee.participant_id]
        # Simulate retained pre-upgrade rows inside this disposable test database only.
        IntakeFieldState.objects.filter(participant_id__in=ids,destination='primary_identifier').delete()
        Participant.objects.filter(pk__in=ids).update(primary_identifier='')
        before=AuditLog.objects.count();report=historical_dry_run()
        self.assertEqual(report['candidate_counts'],{'student':1,'employee':1})
        self.assertEqual(report['reliable_unset_candidates'],2)
        self.assertEqual(Participant.objects.filter(pk__in=ids,primary_identifier='').count(),2)
        self.assertEqual(AuditLog.objects.count(),before)

    def test_identifier_filter_is_saved_and_reloaded_through_existing_views(self):
        self.submit(current_situation='college')
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.post(reverse('participants:saved-views')+'?identifier=student',{'name':'SYNTHETIC student search'})
        self.assertEqual(response.status_code,302)
        from .workspace_models import SavedParticipantView
        saved=SavedParticipantView.objects.get(owner=self.admin,name='SYNTHETIC student search')
        self.assertEqual(saved.criteria['identifier'],'student')
        page=self.client.get(reverse('participants:staff-directory'),{'saved':saved.pk})
        self.assertEqual(page.status_code,200);self.assertEqual(page.context['total_participants'],1)
        self.assertEqual(page.context['criteria']['identifier'],'student')

    def test_pending_identity_intake_is_not_a_reliable_historical_candidate(self):
        source=self.submit(current_situation='college',email='synthetic-pending-source@example.test',phone='5550189001')
        # Reusing one contact creates a genuine unresolved intake review.
        pending=self.submit(current_situation='employed_other',current_industry='retail',email='synthetic-pending-source@example.test',phone='5550189002')
        from apps.events.models import ParticipantMatchReview
        self.assertTrue(ParticipantMatchReview.objects.filter(source_submission=pending,status='open').exists())
        report=historical_dry_run()
        self.assertEqual(report['unprojected_or_review_pending_submissions'],1)
        self.assertEqual(report['reliable_unset_candidates'],0)
        self.assertEqual(report['writes'],0)

    def test_identity_reversal_requires_review_of_alias_derived_identifier(self):
        from .identifier_services import protect_identifier_reversal
        from .intake_projection import _digest
        source=self.submit(current_situation='college')
        target=Participant.objects.create(first_name='SYNTHETIC',last_name='Canonical review target')
        Participant.objects.filter(pk=target.pk).update(primary_identifier='student')
        IntakeFieldState.objects.create(participant=target,destination='primary_identifier',source_submission=source,
            last_value='student',value_hash=_digest('student'),is_protected=False,
            provenance={'origin':'intake','value_source_id':source.pk,'watermark':[source.submitted_at.isoformat(),source.pk]})
        members={source.participant_id,target.pk}
        with transaction.atomic(),self.assertRaises(ValidationError):protect_identifier_reversal(members)
        self.manual(target,'student')
        with transaction.atomic():protect_identifier_reversal(members)
        target.refresh_from_db();self.assertEqual(target.primary_identifier,'student')

    def test_delayed_offline_offset_is_compared_by_utc_instant(self):
        from datetime import timezone as datetime_timezone
        from .identifier_services import project_identifier
        from .models import RegistrationSubmission
        import uuid
        source=self.submit(current_situation='college');person=source.participant
        newer_at=source.submitted_at+timedelta(hours=1)
        newer=RegistrationSubmission.objects.create(form_version=self.version,participant=person,status='submitted',
            intake_operation_id=uuid.uuid4(),submitted_at=newer_at,answers={'current_situation':'college'},
            intake_metadata={'answer_states':{'current_situation':'answered'}})
        older_at=(newer_at-timedelta(minutes=30)).astimezone(datetime_timezone(timedelta(hours=5,minutes=30)))
        older=RegistrationSubmission.objects.create(form_version=self.version,participant=person,status='submitted',
            intake_operation_id=uuid.uuid4(),submitted_at=older_at,answers={'current_situation':'employed_other'},
            intake_metadata={'answer_states':{'current_situation':'answered'}})
        with transaction.atomic():
            target=Participant.objects.select_for_update().get(pk=person.pk)
            self.assertEqual(project_identifier(target,newer),'mapped')
            self.assertEqual(project_identifier(target,older),'older_or_replay')
        person.refresh_from_db();self.assertEqual(person.primary_identifier,'student')
        owner=IntakeFieldState.objects.get(participant=person,destination='primary_identifier')
        self.assertTrue(owner.provenance['watermark'][0].endswith('+00:00'))
