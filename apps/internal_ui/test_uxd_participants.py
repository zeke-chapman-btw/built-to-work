"""Participant presentation contracts over existing identity and audited services."""
from datetime import date
from urllib.parse import urlencode,urlsplit,parse_qs
from types import SimpleNamespace
from uuid import uuid4
import json
from django.test import SimpleTestCase,TestCase,RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone
from apps.internal_ui.people_editors import people_return,directory_origin
from apps.internal_ui.record_editors import local_return
from apps.participants.models import Participant,ParticipantCareerProfile,RegistrationSubmission
from apps.participants.workspace_services import create_person,save_note,profile_revision,duplicate_fingerprint
from apps.participants.workspace_models import ParticipantNote,SavedParticipantView
from apps.participants.workspace_forms import DiscoveryForm
from apps.participants.templatetags.people_ui import people_filter_groups
from apps.participants.participant_document_models import ParticipantDocumentCategory
from apps.participants.intake_lifecycle import create_form
from apps.participants.intake_review import review_revision
from apps.events.models import ParticipantMatchReview


class PeopleReturnTests(SimpleTestCase):
    def test_profile_and_nested_filters_survive_without_external_destinations(self):
        f=RequestFactory();person=SimpleNamespace(pk=4);listing=reverse('participants:staff-directory')+'?q=Jordan&state=GA&page=2'
        profile=reverse('participants:staff-profile',args=[4])+'?'+urlencode({'section':'notes','from':listing})
        request=f.post('/',{'return_to':profile});self.assertEqual(people_return(request,person),profile)
        self.assertEqual(directory_origin(request),listing)
        for bad in ['https://outside.invalid/','//outside.invalid/','/\\outside.invalid','/admin/',profile+'&next=https://outside.invalid',profile.replace('section=notes','section=unsupported')]:
            with self.subTest(bad=bad):self.assertEqual(people_return(f.post('/',{'return_to':bad}),person),reverse('participants:staff-directory'))
        nested=reverse('participants:staff-participant-documents',args=[4])+'?'+urlencode({'from':listing,'return_to':'https://outside.invalid/'})
        result=people_return(f.post('/',{'return_to':nested}),person);self.assertNotIn('outside.invalid',result);self.assertEqual(directory_origin(f.post('/',{'return_to':result})),listing)

    def test_long_chart_context_uses_explicit_limits_without_changing_event_defaults(self):
        listing=reverse('participants:staff-directory')
        dimensions=['channel','state','employment','experience','interest','qualification','document','verification','participation','assessment','completeness','acquisition_month']
        filters=listing+'?'+urlencode({'segments':json.dumps([[key,'x'*220] for key in dimensions]),'q':'Jordan'})
        self.assertGreater(len(filters),2048)
        self.assertEqual(people_return(RequestFactory().get('/',{'return_to':filters})),filters)
        self.assertEqual(local_return(filters,fallback=listing,allowed={listing:{'segments','q'}}),listing)

    def test_document_search_origin_survives_profile_and_nested_editor(self):
        origin=reverse('participants:staff-document-directory')+'?q=Jordan&page=2';profile=reverse('participants:staff-profile',args=[4])+'?'+urlencode({'section':'documents','from':origin})
        request=RequestFactory().get('/',{'return_to':profile})
        self.assertEqual(directory_origin(request),origin);self.assertEqual(people_return(request,SimpleNamespace(pk=4)),profile)


class PeopleWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model();cls.admin=U.objects.create_superuser('uxd-admin','admin@example.invalid','synthetic-only');cls.reader=U.objects.create_user('uxd-reader',is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(content_type__app_label='participants',codename='view_participant'))
        cls.person=create_person(actor=cls.admin,data=dict(first_name='Synthetic Jordan',last_name='Torres',contact_email='jordan.test@example.invalid',city='Augusta',state='GA',audit_reason='Synthetic test fixture'))
        cls.career=ParticipantCareerProfile.objects.create(participant=cls.person,career_interests='Welding',skills_interests='Blueprint reading')
        cls.note=save_note(actor=cls.admin,participant_id=cls.person.pk,data=dict(content='PRIVATE-UXD-NOTE',request_key=uuid4(),audit_reason='Synthetic test'))
        cls.category=ParticipantDocumentCategory.objects.create(name='Synthetic evidence',slug='uxd-evidence')

    def setUp(self):self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
    def profile_data(self,**extra):
        self.person.refresh_from_db()
        values=dict(first_name=self.person.first_name,last_name=self.person.last_name,preferred_name='',contact_email=self.person.contact_email,contact_phone='',city='Augusta',state='GA',postal_code='',revision=profile_revision(self.person),audit_reason='Synthetic correction')
        values.update({'career-'+name:'' for name in ['employment_status','employment_interest','career_interests','skills_interests','years_of_experience','education_training','work_availability','willing_to_travel','preferred_locations','preferred_work_type']});values['career-career_interests']='Welding';values.update(extra);return values

    def test_profile_edit_validation_and_success_retain_origin(self):
        origin=reverse('participants:staff-profile',args=[self.person.pk])+'?'+urlencode({'section':'career','from':reverse('participants:staff-directory')+'?q=Jordan&state=GA&page=2'})
        url=reverse('participants:staff-edit',args=[self.person.pk]);data=self.profile_data(first_name='Retained name',contact_email='invalid',return_to=origin)
        response=self.client.post(url,data);self.assertEqual(response.status_code,400);self.assertContains(response,'Retained name',status_code=400);self.assertEqual(response.context['return_url'],origin)
        data['contact_email']='new.test@example.invalid';response=self.client.post(url,data);self.assertRedirects(response,origin,fetch_redirect_response=False)

    def test_main_participants_entry_opens_the_directory_and_keeps_search_context(self):
        origin=reverse('internal:workspace',args=['participants'])+'?q=Jordan&state=GA'
        response=self.client.get(origin);self.assertEqual(response.status_code,200);self.assertEqual(response.context['active_participant_tab'],'directory');self.assertEqual(response.context['filter_form']['q'].value(),'Jordan')
        profile=self.client.get(reverse('participants:staff-profile',args=[self.person.pk]),{'from':origin});self.assertEqual(profile.context['directory_origin'],origin)

    def test_profile_conflict_retains_old_revision_and_refuses_overwrite(self):
        data=self.profile_data(first_name='My retained draft');old=data['revision'];Participant.objects.filter(pk=self.person.pk).update(city='Other editor',updated_at=timezone.now())
        response=self.client.post(reverse('participants:staff-edit',args=[self.person.pk]),data)
        self.assertEqual(response.status_code,400);self.assertTrue(response.context['save_conflict']);self.assertEqual(response.context['form']['revision'].value(),old)
        self.assertContains(response,'My retained draft',status_code=400);self.person.refresh_from_db();self.assertEqual(self.person.city,'Other editor')

    def test_duplicate_review_keeps_values_and_requires_explicit_distinct_confirmation(self):
        url=reverse('participants:staff-add');before=Participant.objects.count();origin=reverse('participants:staff-directory')+'?q=Synthetic'
        data=dict(first_name='Different synthetic',last_name='Person',contact_email=self.person.contact_email,audit_reason='Synthetic test',return_to=origin)
        response=self.client.post(url,data);self.assertEqual(response.status_code,400);self.assertEqual(Participant.objects.count(),before)
        self.assertContains(response,'Different synthetic',status_code=400);self.assertTrue(response.context['possible_matches']);token=response.context['form']['duplicate_revision'].value();self.assertTrue(token)
        data.update(confirm_distinct='on',duplicate_revision=token);response=self.client.post(url,data);self.assertEqual(response.status_code,302)
        self.assertEqual(Participant.objects.count(),before+1);self.assertIn('from=',response.url)
        self.person.refresh_from_db();self.assertEqual(self.person.first_name,'Synthetic Jordan')

    def test_compact_note_save_is_idempotent_and_returns_to_own_profile_context(self):
        origin=reverse('participants:staff-profile',args=[self.person.pk])+'?'+urlencode({'section':'notes','from':reverse('participants:staff-directory')+'?q=Jordan'})
        url=reverse('participants:note-add',args=[self.person.pk]);before=ParticipantNote.objects.count();get=self.client.get(url,{'return_to':origin},HTTP_X_BTW_EDITOR='1');self.assertEqual(get.status_code,200);self.assertEqual(ParticipantNote.objects.count(),before)
        data=dict(content='Synthetic new private note',request_key=str(uuid4()),audit_reason='Synthetic test',return_to=origin)
        for _ in range(2):self.assertEqual(self.client.post(url,data,HTTP_X_BTW_EDITOR='1').json()['url'],origin)
        self.assertEqual(ParticipantNote.objects.count(),before+1)

    def test_note_stale_edit_has_a_blocked_save_and_retains_entries(self):
        token=self.note.updated_at.isoformat();ParticipantNote.objects.filter(pk=self.note.pk).update(content='Another author version',updated_at=timezone.now())
        response=self.client.post(reverse('participants:note-edit',args=[self.person.pk,self.note.pk]),dict(content='My retained note',revision=token,audit_reason='Synthetic test',request_key=str(self.note.request_key)),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,409);self.assertTrue(response.context['save_conflict']);self.assertContains(response,'My retained note',status_code=409)
        self.note.refresh_from_db();self.assertEqual(self.note.content,'Another author version')

    def test_saved_view_dialog_keeps_filters_and_private_ownership(self):
        origin=reverse('participants:staff-directory')+'?trade_interest=Welding'
        response=self.client.post(reverse('participants:saved-views')+'?trade_interest=Welding',dict(name='Synthetic welding view',return_to=origin),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.json()['url'],origin);saved=SavedParticipantView.objects.get(owner=self.admin,name='Synthetic welding view');self.assertEqual(saved.criteria['trade_interest'],'Welding')
        self.client.force_login(self.reader,backend='django.contrib.auth.backends.ModelBackend');self.assertEqual(self.client.get(reverse('participants:saved-view-archive',args=[saved.pk])).status_code,404)

    def test_saved_view_still_rejects_unsupported_filter_criteria(self):
        before=SavedParticipantView.objects.count();response=self.client.post(reverse('participants:saved-views')+'?unsupported_filter=1',dict(name='Rejected synthetic view',return_to=reverse('participants:staff-directory')),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,400);self.assertEqual(SavedParticipantView.objects.count(),before)
        self.assertContains(response,'unsupported criteria',status_code=400)

    def test_category_editor_retains_record_identity_and_does_not_create_duplicate(self):
        url=reverse('participants:staff-document-categories');before=ParticipantDocumentCategory.objects.count()
        response=self.client.get(url,{'edit':self.category.pk},HTTP_X_BTW_EDITOR='1');self.assertEqual(response.status_code,200);self.assertContains(response,'name="category_id"')
        response=self.client.post(url,dict(category_id=self.category.pk,name='Synthetic renamed evidence',slug='uxd-evidence',description='',position=0,is_active='on',return_to=url),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.json()['url'],url);self.assertEqual(ParticipantDocumentCategory.objects.count(),before);self.category.refresh_from_db();self.assertEqual(self.category.name,'Synthetic renamed evidence')

    def test_limited_profile_does_not_expose_notes_credentials_or_full_birthdate(self):
        Participant.objects.filter(pk=self.person.pk).update(date_of_birth=date(2010,6,15));self.client.force_login(self.reader,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('participants:staff-profile',args=[self.person.pk]));self.assertEqual(response.status_code,200)
        for value in ['PRIVATE-UXD-NOTE','2010-06-15','June 15, 2010']:self.assertNotContains(response,value)
        for section in ['notes','documents','qualifications','assessments','events','activity']:
            self.assertEqual(self.client.get(reverse('participants:staff-profile',args=[self.person.pk]),{'section':section}).status_code,403)

    def test_filter_grouping_keeps_all_authorized_controls_and_no_forbidden_filters(self):
        form=DiscoveryForm(user=self.reader);groups=people_filter_groups({'filter_form':form});names=[field.name for group in groups for field in group['fields']]
        self.assertCountEqual(names,[f.name for f in form.visible_fields() if f.name not in {'q','sort'}]);self.assertNotIn('document_status',names);self.assertNotIn('qualification',names)

    def test_stale_intake_review_retains_reason_and_original_revision(self):
        draft=create_form(actor=self.admin,name='Synthetic review form');source=RegistrationSubmission.objects.create(form_version=draft,participant=self.person,status='submitted',answers=dict(first_name='Synthetic Jordan',last_name='Torres'),intake_operation_id=uuid4())
        review=ParticipantMatchReview.objects.create(source_submission=source,source_participant=self.person,candidates=[self.person.pk],conflict_reasons=['name_conflict'],created_at=timezone.now(),updated_at=timezone.now());token=review_revision(review)
        ParticipantMatchReview.objects.filter(pk=review.pk).update(updated_at=timezone.now())
        response=self.client.post(reverse('participants:intake-review-detail',args=[review.pk]),dict(action='review_later',reason='My retained review reason',revision=token,confirmed='yes'))
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['save_conflict']);self.assertEqual(response.context['revision'],token);self.assertContains(response,'My retained review reason')
