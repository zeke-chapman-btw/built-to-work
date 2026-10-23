"""Assessment privacy, canonical ownership, historical results and profile refinements."""
import base64
import uuid
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.assessments.models import AssessmentCategory, QuestionSet, QuizAttempt, AssessmentSection, AssessmentResponse
from apps.events.models import Event, EventRegistration, ParticipantMatchReview, ParticipantIdentityLink, QrTicket, TicketAllocationBlock
from apps.stations.models import ExperienceSession, Station
from apps.simulators.models import SimulatorCapture, SimulatorCaptureProfile
from .models import (Participant, ParticipantAccount, ParticipantCareerProfile, HistoricalImportBatch,
    HistoricalSimulatorScore, RegistrationForm, RegistrationFormVersion, RegistrationSubmission, PortalPreviewFixture)
from .portal_assessments import profile_assessment_results
from .workspace_models import ParticipantWorkExperience


class ProfileRefinementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('synthetic-refinement-person')
        self.person = Participant.objects.create(first_name='SYNTHETIC', last_name='Profile', preferred_name='Alex', city='Example City', state='GA')
        ParticipantAccount.objects.create(user=self.user, participant=self.person, login_email='refinement@example.test', status='active', email_verified_at=timezone.now())
        self.career = ParticipantCareerProfile.objects.create(participant=self.person, career_interests='Welding')
        self.other = Participant.objects.create(first_name='SYNTHETIC', last_name='Other')
        self.client.force_login(self.user, backend='apps.participants.auth.ParticipantAuthenticationBackend')

    def session(self, person=None, event=None):
        person = person or self.person
        now = timezone.now()
        event = event or Event.objects.create(name='SYNTHETIC assessment ' + uuid.uuid4().hex[:8],
            start_at=now-timedelta(days=2), end_at=now-timedelta(days=2,hours=-4), status='completed')
        registration = EventRegistration.objects.create(event=event, participant=person, source='staff')
        return ExperienceSession.objects.create(event=event, registration=registration, participant=person,
            mode='official', started_at=now-timedelta(hours=1), completed_at=now)

    def quiz(self, person=None, session=None, when=None):
        session = session or self.session(person)
        now = when or timezone.now()
        attempt = QuizAttempt.objects.create(experience_session=session, participant=session.participant,
            status='complete', completed_at=now, is_official=True, void_reason='PRIVATE evaluator reason')
        for position in (1, 2):
            category = AssessmentCategory.objects.create(name='SYNTHETIC Category '+uuid.uuid4().hex, is_active=False)
            question_set = QuestionSet.objects.create(category=category, version=1, name='SYNTHETIC frozen test set')
            section = AssessmentSection.objects.create(attempt=attempt, category=category, question_set=question_set,
                position=position, category_name_snapshot='SYNTHETIC Stored Area '+str(position),
                questions_snapshot=[{'position':1,'text':'PRIVATE question bank','choices':[{'key':'a','correct':True,'text':'PRIVATE answer key'}]}],
                status='complete', completed_at=now)
            AssessmentResponse.objects.create(section=section, question_position=1, selected_choice_key='a', is_correct=True)
        return attempt

    def capture(self, person=None, session=None, when=None, score='83.33'):
        session = session or self.session(person)
        station = Station.objects.create(code='SYN-'+uuid.uuid4().hex[:12], name='SYNTHETIC Simulator', station_type='simulator')
        profile = SimulatorCaptureProfile.objects.create(name='SYNTHETIC Capture '+uuid.uuid4().hex, simulator_type='SYNTHETIC Excavator')
        return SimulatorCapture.objects.create(submission_id=uuid.uuid4(), request_fingerprint='a'*64,
            event=session.event, station=station, profile=profile, profile_version='1', profile_snapshot={},
            raw_identifier='PRIVATE raw identifier', normalized_identifier='private', raw_total_score=score,
            total_score=Decimal(score), captured_at=when or timezone.now(), status='matched', mode='official',
            is_official=True, registration=session.registration, participant=session.participant, experience_session=session,
            diagnostic_metadata={'private':'PRIVATE diagnostics'}, resolution_reason='PRIVATE staff judgment')

    def results(self, page=1):
        return profile_assessment_results(self.user, page)

    def test_name_led_read_first_profile_and_natural_empty_assessments(self):
        response = self.client.get(reverse('participants:profile'))
        self.assertContains(response, '<h1>Alex Profile</h1>', html=True)
        self.assertContains(response, 'Example City, GA')
        self.assertContains(response, 'Assessment Results')
        self.assertContains(response, 'eligible assessment results will appear here')
        self.assertNotContains(response, '100%')
        self.assertNotContains(response, 'visible to employers')
        self.assertNotContains(response, '<input type="text"')
        self.assertNotContains(response, '<textarea')

    def test_header_only_uses_existing_current_role(self):
        ParticipantWorkExperience.objects.create(participant=self.person, created_by=self.user,
            source='participant', role='SYNTHETIC Current Operator', employer='SYNTHETIC Workshop', is_current=True)
        response = self.client.get(reverse('participants:profile'))
        self.assertEqual(response.context['profile_current_role'], 'SYNTHETIC Current Operator')
        self.assertContains(response, 'SYNTHETIC Current Operator')

    def test_finalized_quiz_uses_frozen_category_labels_and_existing_correct_counts(self):
        quiz = self.quiz()
        rows = list(self.results()['assessment_page'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'Quiz')
        self.assertEqual(rows[0]['score'], '2 correct')
        self.assertEqual(rows[0]['date'], quiz.completed_at)
        self.assertEqual(rows[0]['categories'], [{'name':'SYNTHETIC Stored Area 1','correct':1}, {'name':'SYNTHETIC Stored Area 2','correct':1}])
        self.assertNotIn('88%', self.client.get(reverse('participants:profile')).content.decode())

    def test_accepted_simulator_uses_recorded_score_and_capture_date(self):
        capture = self.capture()
        row = list(self.results()['assessment_page'])[0]
        self.assertEqual((row['type'], row['score'], row['date']), ('Simulator', '83.33%', capture.captured_at))
        capture.status='resolved';capture.total_score=Decimal('0');capture.save()
        self.assertEqual(list(self.results()['assessment_page'])[0]['score'], '0%')

    def test_cross_participant_and_forged_query_do_not_expose_results(self):
        own = self.quiz();other = self.quiz(self.other);self.capture(self.other)
        response = self.client.get(reverse('participants:profile'), {'participant':self.other.pk})
        rows = list(response.context['assessment_page'])
        self.assertEqual([r['key'] for r in rows], ['quiz-'+str(own.pk)])
        self.assertNotContains(response, other.experience_session.event.name)

    def test_internal_payload_is_not_exposed(self):
        self.quiz();self.capture()
        response = self.client.get(reverse('participants:profile'))
        for secret in ('PRIVATE evaluator reason','PRIVATE question bank','PRIVATE answer key','PRIVATE diagnostics','PRIVATE raw identifier','PRIVATE staff judgment'):
            self.assertNotContains(response, secret)
        for row in response.context['assessment_page']:
            self.assertEqual(set(row), {'key','type','date','event','timezone','score','categories'})

    def test_unfinished_nonofficial_and_voided_quizzes_stay_hidden(self):
        for changes in ({'status':'active'}, {'is_official':False}, {'voided_at':timezone.now()}, {'completed_at':None}, {'status':'void'}):
            quiz=self.quiz();QuizAttempt.objects.filter(pk=quiz.pk).update(**changes)
        self.assertEqual(self.results()['assessment_count'], 0)

    def test_incomplete_category_sections_stay_hidden(self):
        quiz=self.quiz();quiz.sections.filter(position=2).update(status='active',completed_at=None)
        self.assertEqual(self.results()['assessment_count'], 0)

    def test_unaccepted_voided_test_and_invalid_simulator_scores_stay_hidden(self):
        for changes in ({'status':'needs_review'},{'mode':'demo'},{'is_official':False},{'voided_at':timezone.now()},{'total_score':None},{'total_score':Decimal('101')}):
            capture=self.capture();SimulatorCapture.objects.filter(pk=capture.pk).update(**changes)
        self.assertEqual(self.results()['assessment_count'], 0)

    def test_mismatched_registration_event_and_person_are_rejected(self):
        quiz=self.quiz();QuizAttempt.objects.filter(pk=quiz.pk).update(participant=self.other)
        capture=self.capture();SimulatorCapture.objects.filter(pk=capture.pk).update(participant=self.other)
        invalid_session=self.session();EventRegistration.objects.filter(pk=invalid_session.registration_id).update(participant=self.other)
        self.quiz(session=invalid_session)
        self.assertEqual(self.results()['assessment_count'], 0)

    def test_revoked_registration_hides_results_without_deleting_history(self):
        quiz=self.quiz();EventRegistration.objects.filter(pk=quiz.experience_session.registration_id).update(status='void')
        self.assertEqual(self.results()['assessment_count'], 0)
        self.assertTrue(QuizAttempt.objects.filter(pk=quiz.pk).exists())

    def test_new_results_appear_and_prior_dates_remain_paginated(self):
        first=self.quiz(when=timezone.now()-timedelta(days=12))
        self.assertEqual(self.results()['assessment_count'], 1)
        for days in range(10):self.quiz(when=timezone.now()-timedelta(days=days))
        page=self.results()['assessment_page'];self.assertEqual(len(page),10);self.assertTrue(page.has_next())
        old=list(self.results(2)['assessment_page']);self.assertEqual(old[0]['key'],'quiz-'+str(first.pk))
        response=self.client.get(reverse('participants:profile'), {'assessment_page':2})
        self.assertContains(response,'Previous results');self.assertEqual(response.context['assessment_count'],11)

    def test_reviewed_canonical_alias_includes_history_and_reversal_removes_it(self):
        alias=self.other;quiz=self.quiz(alias)
        form=RegistrationForm.objects.create(slug='synthetic-reviewed-alias',name='SYNTHETIC Review')
        version=RegistrationFormVersion.objects.create(form=form,version=1,snapshot={})
        submission=RegistrationSubmission.objects.create(form_version=version,participant=alias)
        review=ParticipantMatchReview.objects.create(source_submission=submission,source_participant=alias,
            candidate_participant=self.person,status='resolved',evidence={})
        link=ParticipantIdentityLink.objects.create(review=review,source_participant=alias,candidate_participant=self.person)
        self.assertEqual(list(self.results()['assessment_page'])[0]['key'],'quiz-'+str(quiz.pk))
        link.is_active=False;link.save()
        self.assertEqual(self.results()['assessment_count'],0)

    def test_imported_historical_scores_have_no_automatic_release(self):
        batch=HistoricalImportBatch.objects.create(source_file='synthetic-review.csv',original_filename='synthetic-review.csv',source_sha256='b'*64,created_by=self.user)
        HistoricalSimulatorScore.objects.create(participant=self.person,raw_score=Decimal('99'),score_method='PRIVATE internal historical method',source='SYNTHETIC import',source_batch=batch,source_row_number=1)
        self.assertEqual(self.results()['assessment_count'],0)

    def test_assessment_section_is_read_only_and_get_preserves_stored_evidence(self):
        quiz=self.quiz();capture=self.capture();before=(quiz.completed_at,capture.total_score,AssessmentResponse.objects.count())
        response=self.client.get(reverse('participants:profile'));section=response.content.decode().split('id="assessments"',1)[1].split('</section>',1)[0]
        self.assertNotIn('<form',section);self.assertNotIn('Add Assessment',section);self.assertNotIn('Edit Assessment',section)
        self.client.post(reverse('participants:profile'), {'quiz_id':str(quiz.pk),'score':'99','participant':self.other.pk})
        quiz.refresh_from_db();capture.refresh_from_db()
        self.assertEqual(before,(quiz.completed_at,capture.total_score,AssessmentResponse.objects.count()))

    def test_document_upload_keeps_profile_shell_and_cancel(self):
        response=self.client.get(reverse('participants:portal-document-add'))
        self.assertContains(response,'enctype="multipart/form-data"')
        self.assertContains(response,'href="/participants/profile/#documents">Cancel')
        self.assertContains(response,'Job Opportunities')
        self.assertNotContains(response,'Back to sign in')

    def test_unauthenticated_and_plain_staff_cannot_read_assessments(self):
        self.client.logout();self.assertEqual(self.client.get(reverse('participants:profile')).status_code,302)
        admin=get_user_model().objects.create_superuser('synthetic-unselected-admin',password='SYNTHETIC-unit-only')
        with self.assertRaises(PermissionDenied):profile_assessment_results(admin)

    @override_settings(BTW_PREVIEW_GATE_ENABLED=True,BTW_PREVIEW_GATE_USERNAME='preview-reviewer',BTW_PREVIEW_GATE_PASSWORD='synthetic-test-only')
    def test_existing_admin_preview_uses_private_fixture_without_new_tickets(self):
        admin=get_user_model().objects.create_superuser('synthetic-result-preview',password='SYNTHETIC-unit-only')
        self.client.force_login(admin,backend='django.contrib.auth.backends.ModelBackend')
        self.client.defaults['HTTP_AUTHORIZATION']='Basic '+base64.b64encode(b'preview-reviewer:synthetic-test-only').decode()
        self.client.post(reverse('internal:portal-preview-enter'));fixture=PortalPreviewFixture.objects.get(administrator=admin)
        session=self.session(fixture.participant,fixture.event);self.quiz(session=session);self.capture(session=session)
        before=(QrTicket.objects.count(),TicketAllocationBlock.objects.count())
        response=self.client.get(reverse('participants:profile'))
        self.assertContains(response,'2 correct');self.assertContains(response,'83.33%')
        self.assertContains(response,'Admin Preview — Participant Portal');self.assertContains(response,'Return to Admin')
        self.assertEqual(before,(QrTicket.objects.count(),TicketAllocationBlock.objects.count()))
        self.assertEqual(self.client.session['_auth_user_id'],str(admin.pk))
        self.assertFalse(ParticipantAccount.objects.filter(participant=fixture.participant).exists())
