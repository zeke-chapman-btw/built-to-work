"""Populated sources, focused manager boundaries and explicit current preferences."""
from datetime import timedelta
from urllib.parse import urlencode
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.recruiting.test_employer import EmployerFixtures
from apps.recruiting.models import RecruitingProfile, Classification
from apps.recruiting.preferences import current_preferences, initial_preferences
from apps.recruiting.foundation import save_preferences
from apps.events.models import Event, EventRegistration
from apps.stations.models import ExperienceSession, ExperienceActivity, Station, EventStation
from apps.assessments.models import QuizAttempt, AssessmentSection, AssessmentResponse, AssessmentCategory, QuestionSet
from apps.simulators.models import SimulatorCaptureProfile, SimulatorCapture
from .models import Participant, ParticipantCareerProfile, RegistrationSubmission
from .card_editors import ProfileCardForm
from .participant_document_models import ParticipantDocument, ParticipantDocumentCategory
from .qualification_models import ParticipantQualification

BACKEND='django.contrib.auth.backends.ModelBackend'

class ProfileFollowupTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.client.force_login(self.admin,backend=BACKEND)
        self.profile=reverse('participants:staff-profile',args=[self.person.pk])
        self.manager=reverse('participants:staff-participant-documents',args=[self.person.pk])

    def get_manager(self, section, **query):
        return self.client.get(self.profile,{'section':section,**query},HTTP_X_BTW_EDITOR='1')

    def test_all_read_managers_are_focused_and_keep_full_route_fallback(self):
        for section in ['career','documents','qualifications','assessments','events','acquisition','notes','activity']:
            with self.subTest(section=section):
                response=self.get_manager(section)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers.get('X-BTW-Editor'),'1')
                self.assertContains(response,'data-editor-panel')
                self.assertNotContains(response,'<html')
        self.assertContains(self.client.get(self.profile),'<html')

    def test_document_and_qualification_manage_use_fragments_and_original_permissions(self):
        for url in [self.manager,reverse('participants:staff-participant-qualifications',args=[self.person.pk])]:
            response=self.client.get(url,HTTP_X_BTW_EDITOR='1')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers.get('X-BTW-Editor'),'1')
            self.assertNotContains(response,'<html')
        for actor in [self.customer,self.portal]:
            self.client.force_login(actor,backend=BACKEND)
            self.assertEqual(self.client.get(self.manager,HTTP_X_BTW_EDITOR='1').status_code,403)

    def test_secondary_acquisition_correction_has_focused_form_and_safe_fallback(self):
        from html.parser import HTMLParser
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.rows=[]
            def handle_starttag(self,tag,attrs):
                if tag=='a':self.rows.append(dict(attrs))
        url=reverse('participants:acquisition-edit',args=[self.person.pk])
        page=self.client.get(self.profile,{'section':'acquisition','tools':'source'})
        queue=reverse('participants:intake-review-queue')
        self.assertContains(page,queue+'?status=open')
        links=Links();links.feed(page.content.decode())
        self.assertTrue(any(link.get('href','').split('?')[0]==url and 'data-record-editor' in link for link in links.rows))
        response=self.client.get(url,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers.get('X-BTW-Editor'),'1')
        self.assertContains(response,'data-editor-panel')
        self.assertContains(response,'How they found us')
        self.assertNotContains(response,'<html')
        self.assertContains(self.client.get(url),'<html')
        reader=get_user_model().objects.create_user(username='SYNTHETIC-source-reader',is_staff=True)
        reader.user_permissions.add(*Permission.objects.filter(content_type__app_label='participants',codename__in=['view_participant','view_registrationsubmission']))
        self.client.force_login(reader,backend=BACKEND)
        restricted=self.client.get(self.profile,{'section':'acquisition','tools':'source'})
        self.assertNotContains(restricted,queue+'?status=open')
        self.assertEqual(self.client.get(queue).status_code,403)

    def test_preference_choices_are_current_status_not_desired_arrangement(self):
        form=ProfileCardForm(card='preferences',person=self.person,actor=self.admin)
        names=list(form.fields)
        visible=[key for key in names if not form.fields[key].widget.is_hidden]
        self.assertEqual(visible[:4],['employment_interest','preferred_work_type','current_employment_status','travel'])
        self.assertEqual(dict(form.fields['employment_interest'].choices),{'':'Not set','seeking':'Yes','open':'Maybe','not_seeking':'No'})
        self.assertEqual([label for key,label in form.fields['current_employment_status'].choices if key and key!='__retained__'],['Full time','Part time','Self employed','Contract','Unemployed'])
        career=ParticipantCareerProfile.objects.get(participant=self.person)
        career.preferred_work_type='part_time';career.employment_status='Legacy nonstandard retained';career.save()
        before=Classification.objects.count()
        from apps.recruiting.forms import PreferencesForm
        initial=initial_preferences(self.person)
        form=PreferencesForm(data={**initial,'current_employment_status':'unemployed','willing_to_relocate':'maybe','reason':'SYNTHETIC explicit facts'},initial=initial)
        self.assertTrue(form.is_valid(),form.errors)
        data=form.cleaned_data
        save_preferences(actor=self.admin,participant_id=self.person.pk,data=data,expected_revision=data['revision'],why='SYNTHETIC explicit current facts')
        facts=current_preferences(self.person)
        self.assertEqual(facts['employment']['value'],'Unemployed')
        self.assertEqual(facts['relocation'],'Maybe')
        career.refresh_from_db()
        self.assertEqual(career.preferred_work_type,'part_time')
        self.assertEqual(career.employment_status,'Legacy nonstandard retained')
        self.assertEqual(Classification.objects.count(),before)
        initial=initial_preferences(self.person)
        form=PreferencesForm(data={**initial,'current_employment_status':'','willing_to_relocate':'','reason':'SYNTHETIC explicit unknown'},initial=initial)
        self.assertTrue(form.is_valid(),form.errors)
        data=form.cleaned_data
        save_preferences(actor=self.admin,participant_id=self.person.pk,data=data,expected_revision=data['revision'],why='SYNTHETIC explicit unknown')
        self.assertEqual(current_preferences(self.person)['employment']['value'],'Unknown')
        self.assertEqual(current_preferences(self.person)['relocation'],'Not recorded')

    def test_original_answer_view_does_not_substitute_edited_current_profile(self):
        submission=RegistrationSubmission.objects.filter(participant=self.person,status='submitted').first()
        self.assertIsNotNone(submission)
        original=dict(submission.answers)
        self.person.city='SYNTHETIC edited city';self.person.save(update_fields=['city'])
        response=self.get_manager('acquisition',submission=submission.pk)
        self.assertContains(response,'Original submission')
        self.assertContains(response,'Version')
        self.assertNotContains(response,'SYNTHETIC edited city')
        submission.refresh_from_db();self.assertEqual(submission.answers,original)
        self.assertTrue(response.context['original_answer_rows'])

    def test_original_versions_keep_pinned_labels_choices_and_event_answers(self):
        from .models import RegistrationForm, RegistrationFormVersion
        form=RegistrationForm.objects.create(slug='followup-retained-versions',name='SYNTHETIC retained versions')
        submissions=[]
        for number,label,value in [(1,'Original trade question','old'),(2,'Revised original trade question','new')]:
            version=RegistrationFormVersion.objects.create(form=form,version=number,snapshot={'questions':[{'canonical_key':'trade','label':label,'options':[{'value':value,'label':'Retained choice '+str(number)}]}]})
            row=RegistrationSubmission.objects.create(form_version=version,participant=self.person,status='submitted',submitted_at=timezone.now(),answers={'trade':value},answer_snapshot={'event_questions':[{'key':'transport','label':'Original event transport question'}],'event_answers':{'transport':{'answer':'SYNTHETIC original bus'}}})
            submissions.append(row)
        retained=list(RegistrationSubmission.objects.filter(pk__in=[x.pk for x in submissions]).values('pk','answers','answer_snapshot'))
        for number,row in enumerate(submissions,1):
            response=self.get_manager('acquisition',submission=row.pk)
            self.assertContains(response,'Retained choice '+str(number))
            self.assertContains(response,'Original event transport question')
            self.assertContains(response,'SYNTHETIC original bus')
            self.assertEqual(response.context['original_submission'].pk,row.pk)
            self.assertTrue({x.pk for x in submissions}.issubset({x.pk for x in response.context['original_submissions']}))
        self.assertEqual(list(RegistrationSubmission.objects.filter(pk__in=[x.pk for x in submissions]).values('pk','answers','answer_snapshot')),retained)

    def test_omitting_new_employment_field_keeps_existing_classification_contract(self):
        from apps.recruiting.forms import PreferencesForm
        choice=Classification.objects.create(kind='employment',key='synthetic-legacy-vocabulary',label='SYNTHETIC custom current classification')
        row,_=RecruitingProfile.objects.get_or_create(participant=self.person)
        row.employment_classification=choice;row.save()
        initial=initial_preferences(self.person)
        payload={**initial,'reason':'SYNTHETIC existing client'}
        payload.pop('current_employment_status')
        form=PreferencesForm(data=payload,initial=initial)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertNotIn('current_employment_status',form.cleaned_data)
        save_preferences(actor=self.admin,participant_id=self.person.pk,data=form.cleaned_data,expected_revision=initial['revision'],why='SYNTHETIC existing client')
        row.refresh_from_db();self.assertIsNone(row.current_employment_status)
        self.assertEqual(current_preferences(self.person)['employment']['value'],choice.label)

    def test_untouched_profile_keeps_predecessor_confirmation_fingerprint(self):
        from apps.recruiting.foundation import profile_fingerprint, freshness
        person=Participant.objects.create(first_name='SYNTHETIC',last_name='Fingerprint retained')
        # Captured by running the actual accepted 4a307a7 function over this
        # deterministic subject; not recomputed from the new implementation.
        previous='24d6b371d973815b6c2f89828c3b655b7b60dd9304301bfdc4f87bfd74d61649'
        row=RecruitingProfile.objects.create(participant=person,last_confirmed_at=timezone.now(),confirmed_fingerprint=previous)
        self.assertEqual(profile_fingerprint(person),previous)
        self.assertFalse(freshness(person)['needs_confirmation'])
        save_preferences(actor=self.admin,participant_id=person.pk,data={'current_employment_status':'unemployed'},expected_revision=0,why='SYNTHETIC explicit new current fact')
        self.assertTrue(freshness(person)['needs_confirmation'])

    def populated_sources(self):
        now=timezone.now()
        event=Event.objects.create(name='SYNTHETIC accepted event',start_at=now-timedelta(hours=1),end_at=now+timedelta(hours=5),status='upcoming')
        registration=EventRegistration.objects.create(event=event,participant=self.person)
        from django.db import transaction
        from apps.events.services import issue_ticket
        from apps.stations.services import activate_event_context,process_station_scan
        kiosk=Station.objects.create(name='SYNTHETIC QR kiosk',code='followup-real-kiosk',station_type='kiosk')
        assignment=EventStation.objects.create(event=event,station=kiosk)
        activate_event_context(assignment=assignment,actor=self.admin)
        ticket=issue_ticket(registration=registration,actor=self.admin)
        self.assertFalse(ExperienceSession.objects.filter(registration=registration).exists())
        with transaction.atomic():
            rejected=process_station_scan(station_code=kiosk.code,token='SYNTHETIC-invalid-token',actor=self.admin)
        self.assertNotEqual(rejected['status'],'activity_in_progress')
        self.assertFalse(ExperienceSession.objects.filter(registration=registration).exists())
        with transaction.atomic():
            accepted=process_station_scan(station_code=kiosk.code,token=ticket.token,actor=self.admin)
            repeated=process_station_scan(station_code=kiosk.code,token=ticket.token,actor=self.admin)
        self.assertEqual(accepted['status'],'activity_in_progress')
        session=accepted['session']
        self.assertEqual(repeated['session'].pk,session.pk)
        self.assertEqual(ExperienceSession.objects.filter(registration=registration).count(),1)
        now=timezone.now()
        category=AssessmentCategory.objects.create(name='SYNTHETIC quiz',slug='followup-quiz')
        question_set=QuestionSet.objects.create(category=category,name='Seven retained questions',version=1)
        attempt=QuizAttempt.objects.create(experience_session=session,participant=self.person,status='complete',selected_at=now,completed_at=now)
        section=AssessmentSection.objects.create(attempt=attempt,category=category,question_set=question_set,position=0,category_name_snapshot='SYNTHETIC seven-question quiz',questions_snapshot=[{'key':str(i)} for i in range(7)])
        for i in range(7):AssessmentResponse.objects.create(section=section,question_position=i,selected_choice_key='A',is_correct=i<4)
        station=Station.objects.create(name='SYNTHETIC Excavator',code='followup-excavator',station_type='simulator')
        profile=SimulatorCaptureProfile.objects.create(name='SYNTHETIC source',simulator_type='Excavator')
        import uuid
        SimulatorCapture.objects.create(submission_id=uuid.uuid4(),request_fingerprint='f'*64,event=event,station=station,profile=profile,profile_version='1',profile_snapshot={'simulator_type':'Excavator'},raw_identifier='synthetic',raw_total_score='73.125',total_score='73.125',captured_at=now,status='matched',mode='official',is_official=True,registration=registration,participant=self.person,experience_session=session)
        return event,session

    def test_populated_quiz_denominator_simulator_raw_units_and_event_deduplication(self):
        event,session=self.populated_sources()
        ExperienceActivity.objects.filter(session=session,activity='simulator').update(status='in_progress',started_at=timezone.now())
        response=self.client.get(self.profile)
        self.assertContains(response,'4/7 correct')
        self.assertNotContains(response,'4/15')
        self.assertContains(response,'73%')
        self.assertNotContains(response,'73.125%')
        self.assertContains(response,'SYNTHETIC seven-question quiz')
        self.assertEqual(len(response.context['participation_events']),1)
        self.assertEqual(response.context['participation_events'][0]['event_id'],event.pk)

    def test_full_result_view_retains_void_quiz_and_original_game_historical_units(self):
        from .models import HistoricalImportBatch, HistoricalEventRecord, HistoricalSimulatorScore
        from apps.games.models import GameDefinition, GameSession
        event,session=self.populated_sources()
        QuizAttempt.objects.filter(experience_session=session).update(status='void',voided_at=timezone.now(),void_reason='SYNTHETIC retained source')
        game=GameDefinition.objects.create(key='followup-retained-game',display_name='SYNTHETIC raw game',implementation_key='synthetic')
        GameSession.objects.create(event=event,experience_session=session,game=game,mode='official',status='completed',game_version='1',configuration_version='1',raw_score=0,completed_at=timezone.now(),result_data={'original_units':'points'})
        batch=HistoricalImportBatch.objects.create(original_filename='SYNTHETIC fixture only',source_file='synthetic-not-uploaded',source_sha256='a'*64,created_by=self.admin)
        old_event=HistoricalEventRecord.objects.create(participant=self.person,event_name='SYNTHETIC historical event',source_batch=batch,source_row_number=1)
        HistoricalSimulatorScore.objects.create(participant=self.person,event_record=old_event,raw_score='8421.1250',score_method='retained raw points',source='SYNTHETIC historical source',source_batch=batch,source_row_number=1)
        overview=self.client.get(self.profile)
        self.assertNotContains(overview,'4/7 correct')
        response=self.get_manager('assessments')
        self.assertContains(response,'4/7 correct')
        self.assertContains(response,'Voided')
        self.assertContains(response,'SYNTHETIC raw game')
        self.assertEqual(list(response.context['game_sessions'])[0].raw_score,0)
        self.assertContains(response,'8421.1250')
        self.assertContains(response,'retained raw points')
        self.assertContains(response,'SYNTHETIC historical source')
        self.assertNotContains(response,'combined score')

    def test_registration_or_unstarted_session_is_not_participation(self):
        now=timezone.now()
        for index in range(2):
            event=Event.objects.create(name=f'SYNTHETIC registration only {index}',start_at=now,end_at=now+timedelta(hours=4))
            registration=EventRegistration.objects.create(event=event,participant=self.person)
            if index:ExperienceSession.objects.create(event=event,registration=registration,participant=self.person,mode='official')
        response=self.get_manager('events')
        self.assertEqual(len(response.context['participation_events']),0)
        self.assertContains(response,'No events yet.')
        self.assertContains(response,'Registration only',count=2)

    def test_read_only_staff_cannot_mutate_through_manager(self):
        user=get_user_model().objects.create_user(username='SYNTHETIC-read-only',is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='participants',codename__in=['view_participant','view_participantdocument','view_participantqualification']))
        self.client.force_login(user,backend=BACKEND)
        self.assertEqual(self.client.get(self.manager,HTTP_X_BTW_EDITOR='1').status_code,200)
        self.assertEqual(self.client.post(self.manager,{'audit_reason':'SYNTHETIC forbidden'},HTTP_X_BTW_EDITOR='1').status_code,403)
        self.assertEqual(self.get_manager('acquisition').status_code,403)
        for card in ['identity','skills','education','preferences']:
            self.assertEqual(self.client.get(reverse('participants:profile-card-edit',args=[self.person.pk,card]),HTTP_X_BTW_EDITOR='1').status_code,403)

    @override_settings(PRIVATE_DOCUMENT_ROOT='/tmp/btw-followup-test-private')
    def test_document_upload_metadata_stale_rejection_and_private_download(self):
        category=ParticipantDocumentCategory.objects.create(name='SYNTHETIC private',slug='followup-private')
        upload=SimpleUploadedFile('synthetic.pdf',b'%PDF-1.4\nSYNTHETIC private fixture\n%%EOF',content_type='application/pdf')
        response=self.client.post(self.manager,{'category':category.pk,'file':upload,'audit_reason':'SYNTHETIC upload','return_to':self.profile},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,200)
        document=ParticipantDocument.objects.get(participant=self.person,category=category)
        url=reverse('participants:staff-document-edit',args=[document.uuid])
        editor=self.client.get(url,HTTP_X_BTW_EDITOR='1')
        old_revision=document.updated_at.isoformat()
        response=self.client.post(url,{'category':category.pk,'description':'SYNTHETIC metadata','audit_reason':'SYNTHETIC edit','expected_revision':old_revision,'return_to':self.profile},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,200)
        stale=self.client.post(url,{'category':category.pk,'description':'SYNTHETIC stale overwrite','audit_reason':'SYNTHETIC stale','expected_revision':old_revision,'return_to':self.profile},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(stale.status_code,409)
        document.refresh_from_db();self.assertEqual(document.description,'SYNTHETIC metadata')
        self.client.force_login(self.customer,backend=BACKEND)
        self.assertEqual(self.client.get(reverse('participants:staff-document-download',args=[document.uuid])).status_code,403)

    def test_staff_history_filter_uses_actor_provenance_and_compact_name(self):
        from apps.core.audit import record_audit
        record_audit(actor=self.admin,action='participant.workspace.synthetic_followup',instance=self.person,source='synthetic',new_data={})
        response=self.get_manager('activity',actor_group='staff')
        self.assertTrue(response.context['timeline'])
        self.assertTrue(all(row['actor_group']=='staff' for row in response.context['timeline']))
        self.assertNotContains(response,'Stored identity retained')
