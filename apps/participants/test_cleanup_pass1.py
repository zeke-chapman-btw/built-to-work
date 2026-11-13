"""Field-scoped profile editing and bounded shared directory contracts."""
import uuid
from urllib.parse import urlencode
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.recruiting.test_employer import EmployerFixtures
from apps.crm import services
from apps.crm.actor_names import compact_actor
from apps.crm.models import Interaction
from apps.core.models import AuditLog
from .models import Participant, ParticipantCareerProfile, IntakeFieldState
from .card_editors import CARDS, ProfileCardForm, save_card
from .workspace_services import profile_revision
from .discovery import discover
from .intake_discovery import effective_travel


class ParticipantCleanupTests(EmployerFixtures, TestCase):
    def setUp(self):
        self.employer_setup()
        self.grant = self.release()
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.profile = reverse('participants:staff-profile', args=[self.person.pk])
        self.directory = reverse('participants:staff-directory')

    def card_data(self, card):
        self.person.refresh_from_db()
        f = ProfileCardForm(card=card, person=self.person, actor=self.admin)
        return {key: f.initial.get(key) if f.initial.get(key) is not None else '' for key in f.fields}

    def card_url(self, card):
        return reverse('participants:profile-card-edit', args=[self.person.pk, card])

    def test_every_scalar_card_saves_only_its_fields_and_audits_without_reason(self):
        self.assertEqual(self.client.get(self.card_url('identity')).context['back'],self.profile)
        career, _ = ParticipantCareerProfile.objects.get_or_create(participant=self.person)
        career.skills_interests = 'SYNTHETIC preserved skills'; career.education_training = 'SYNTHETIC preserved education'
        career.save()
        for card, field, value in [('identity','city','Augusta'), ('skills','skills_interests','SYNTHETIC revised skills'), ('education','education_training','SYNTHETIC revised training'), ('preferences','career_interests','SYNTHETIC Welding')]:
            with self.subTest(card=card):
                before = ParticipantCareerProfile.objects.get(participant=self.person)
                payload = self.card_data(card); payload[field] = value
                origin = self.profile + '?' + urlencode({'section': 'overview', 'from': self.directory})
                payload['return_to'] = origin
                response = self.client.post(self.card_url(card), payload, HTTP_X_BTW_EDITOR='1')
                self.assertEqual(response.status_code, 200, response.content[:800])
                self.assertEqual(response.json()['url'], origin)
                current = Participant.objects.get(pk=self.person.pk) if card == 'identity' else ParticipantCareerProfile.objects.get(participant=self.person)
                self.assertEqual(getattr(current, field), value)
                if card != 'education':
                    self.assertEqual(ParticipantCareerProfile.objects.get(participant=self.person).education_training, before.education_training)
        self.assertEqual(AuditLog.objects.filter(action='participant.workspace.profile.card_updated').count(), 4)

    def test_stale_or_extra_field_saves_cannot_overwrite_other_cards(self):
        data = self.card_data('identity')
        self.person.city = 'SYNTHETIC concurrent city'; self.person.save()
        data['city'] = 'SYNTHETIC stale city'
        r = self.client.post(self.card_url('identity'), data, HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,409)
        self.assertContains(r,'SYNTHETIC stale city',status_code=409)
        self.person.refresh_from_db(); self.assertEqual(self.person.city,'SYNTHETIC concurrent city')
        data=self.card_data('education'); data['first_name']='FORBIDDEN extra field'
        self.assertEqual(self.client.post(self.card_url('education'),data,HTTP_X_BTW_EDITOR='1').status_code,400)
        self.person.refresh_from_db();self.assertNotEqual(self.person.first_name,'FORBIDDEN extra field')

    def test_type_editor_in_profile_card_uses_ownership_revision_and_protection(self):
        from .identifier_definitions import PrimaryIdentifier
        # Participant type is edited from the Details card now (with how they found us).
        url=reverse('participants:acquisition-edit',args=[self.person.pk])
        form=self.client.get(url,HTTP_X_BTW_EDITOR='1').context['form']
        data={'participant_type':PrimaryIdentifier.values[0],'type_revision':form.initial['type_revision'],'channel':'unknown','revision':form.initial['revision']}
        self.assertEqual(self.client.post(url,data,HTTP_X_BTW_EDITOR='1').status_code,200)
        state=IntakeFieldState.objects.get(participant=self.person,destination='primary_identifier')
        self.assertTrue(state.is_protected)
        self.assertEqual(state.provenance['origin'],'staff')
        # A second save from the same (now stale) Details form is refused instead of overwriting.
        state.save();data['participant_type']=PrimaryIdentifier.values[1]
        self.assertIn(self.client.post(url,data,HTTP_X_BTW_EDITOR='1').status_code,(400,409))
        self.person.refresh_from_db();self.assertEqual(self.person.primary_identifier,PrimaryIdentifier.values[0])

    def test_typed_maybe_unknown_and_unrelated_preferences_are_preserved(self):
        for value in ('yes','maybe','no',''):
            data=self.card_data('preferences');data['travel']=value
            response=self.client.post(self.card_url('preferences'),data,HTTP_X_BTW_EDITOR='1')
            self.assertEqual(response.status_code,200,response.content[:500])
            career=ParticipantCareerProfile.objects.get(participant=self.person)
            state=IntakeFieldState.objects.filter(participant=self.person,destination='travel_willingness').first()
            self.assertEqual(effective_travel(career,state) or '',value)
            f, qs, criteria=discover(self.admin,{'q':'#'+str(self.person.pk),'travel':value or 'unknown'})
            self.assertTrue(f.is_valid(),f.errors)
            self.assertEqual(list(qs.values_list('pk',flat=True)),[self.person.pk])
        data=self.card_data('preferences');data['career_interests']='SYNTHETIC changed trades'
        self.client.post(self.card_url('preferences'),data,HTTP_X_BTW_EDITOR='1')
        self.assertIsNone(ParticipantCareerProfile.objects.get(participant=self.person).willing_to_travel)

    def test_filter_labels_preserve_stored_interest_distinctions_and_any_vs_unknown(self):
        records=[]
        for interest,travel in [('seeking','yes'),('open','maybe'),('not_seeking','no'),('',None)]:
            p=Participant.objects.create(first_name='SYNTHETIC',last_name='Filter '+str(len(records)))
            ParticipantCareerProfile.objects.create(participant=p,employment_interest=interest,intake_data={'travel_willingness':travel})
            records.append(p)
        for key,values in [('employment',['seeking','open','not_seeking','unknown']),('travel',['yes','maybe','no','unknown'])]:
            for index,value in enumerate(values):
                f,qs,_=discover(self.admin,{'q':'SYNTHETIC Filter',key:value})
                self.assertTrue(f.is_valid(),f.errors)
                self.assertEqual(set(qs.values_list('pk',flat=True)),{records[index].pk})
            _,qs,_=discover(self.admin,{'q':'SYNTHETIC Filter',key:''})
            self.assertEqual(qs.count(),4)
        response=self.client.get(self.directory)
        for key in ('employment','travel'):
            group=next(x for x in response.context['explorer_groups'] if x['key']==key)
            self.assertEqual([o['label'] for o in group['options']],['Any','Yes','Maybe','No','Unknown'])

    def test_pagination_is_bounded_stable_and_preserves_query(self):
        Participant.objects.bulk_create([Participant(first_name='SYNTHETIC Page',last_name=f'{i:03d}') for i in range(31)])
        first=self.client.get(self.directory,{'q':'SYNTHETIC Page','page_size':'10','page':1})
        second=self.client.get(self.directory,{'q':'SYNTHETIC Page','page_size':'10','page':2})
        self.assertEqual(len(first.context['page_obj']),10)
        self.assertEqual(first.context['page_obj'].paginator.count,31)
        self.assertFalse({p.pk for p in first.context['page_obj']} & {p.pk for p in second.context['page_obj']})
        self.assertContains(first,'1–10 of 31')
        self.assertContains(first,'page_size=10')
        self.assertEqual(self.client.get(self.directory,{'page_size':'1000000'}).status_code,400)

    def test_shared_directory_sections_and_saved_searches(self):
        for url in [self.directory,reverse('customers:directory')]:
            for listing in ['search','activity','followups']:
                r=self.client.get(url,{'listing':listing})
                self.assertEqual(r.status_code,200)
                self.assertEqual(r.context['directory_listing'],listing)
                self.assertContains(r,'aria-label="Directory sections"')
                if listing!='search':self.assertNotContains(r,'role="search"')
        r=self.client.get(self.directory)
        self.assertContains(r,'Saved searches')
        self.assertNotContains(r,'Views &amp; analysis')
        self.assertNotContains(r,'Existing staff workflows')
        r=self.client.get(self.directory,{'listing':'activity','employment':'invalid','page_size':'999999'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.context['directory_listing'],'activity')
        self.assertNotIn('page_obj',r.context)

    def test_modal_interaction_origin_validation_and_original_actor(self):
        self.admin.first_name='Zeke';self.admin.last_name='Chapman';self.admin.save()
        url=reverse('crm:participant-new',args=[self.person.pk])
        origin=self.profile+'?'+urlencode({'section':'overview','from':self.directory})
        r=self.client.get(url,{'return_to':origin},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'data-editor-form')
        f=r.context['form'];data={'operation_id':str(f.initial.get('operation_id') or uuid.uuid4()),'activity_type':'call','occurred_at':timezone.localtime().strftime('%Y-%m-%dT%H:%M'),'summary':'SYNTHETIC modal touchpoint','return_to':origin}
        invalid=self.client.post(url,{**data,'summary':''},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(invalid.status_code,400)
        r=self.client.post(url,data,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.json()['url'],origin)
        row=Interaction.objects.get(summary=data['summary'])
        self.assertEqual(compact_actor(row.created_by),'Zeke C.')
        edit=reverse('crm:correct',args=[row.pk]);data.update(revision=row.revision,reason='SYNTHETIC correction',summary='SYNTHETIC corrected')
        r=self.client.post(edit,data,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,200,r.content[:400])
        self.assertEqual(r.json()['url'],origin)
        row.refresh_from_db();self.assertEqual(row.created_by,self.admin)
        unsafe=self.client.get(url,{'return_to':'https://example.test/steal'},HTTP_X_BTW_EDITOR='1')
        self.assertNotEqual(unsafe.context['back'],'https://example.test/steal')

    def test_permission_gates_archived_and_invalid_values(self):
        r=self.client.post(self.card_url('identity'),{**self.card_data('identity'),'contact_email':'bad email'},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,400)
        for role in [self.customer,self.portal]:
            self.client.force_login(role,backend='django.contrib.auth.backends.ModelBackend')
            self.assertIn(self.client.get(self.card_url('identity')).status_code,[302,403])
        self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')
        self.person.archived_at=timezone.now();self.person.save()
        self.assertIn(self.client.get(self.card_url('identity')).status_code,[403,404])

    def test_current_classifications_use_existing_recruiting_delegation_and_revision(self):
        from apps.recruiting.models import Classification, RecruitingProfile
        row, _ = RecruitingProfile.objects.get_or_create(participant=self.person)
        choice = Classification.objects.create(kind='employment',key='synthetic-cleanup',label='SYNTHETIC Employed')
        data=self.card_data('preferences'); data['employment_classification']=str(choice.pk)
        r=self.client.post(self.card_url('preferences'),data,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(r.status_code,200,r.content[:500])
        row.refresh_from_db(); self.assertEqual(row.employment_classification,choice)
        stale=self.card_data('preferences');row.revision+=1;row.save()
        self.assertEqual(self.client.post(self.card_url('preferences'),stale,HTTP_X_BTW_EDITOR='1').status_code,409)
        staff=get_user_model().objects.create_user(username='synthetic-card-staff',is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=['view_participant','change_participant']))
        self.client.force_login(staff,backend='django.contrib.auth.backends.ModelBackend')
        form=self.client.get(self.card_url('preferences'),HTTP_X_BTW_EDITOR='1').context['form']
        self.assertNotIn('employment_classification',form.fields)
        data={key:form.initial.get(key) if form.initial.get(key) is not None else '' for key in form.fields}
        data['employment_classification']=''
        self.assertEqual(self.client.post(self.card_url('preferences'),data,HTTP_X_BTW_EDITOR='1').status_code,400)
        row.refresh_from_db();self.assertEqual(row.employment_classification,choice)

    def test_summary_counts_authorized_records_and_unavailable_is_not_zero(self):
        services.create_internal(actor=self.admin,participant_id=self.person.pk,organization_id=self.organization.pk,
            data={'activity_type':'call','summary':'SYNTHETIC customer-linked record','occurred_at':timezone.now()},operation_id=uuid.uuid4())
        services.create_internal(actor=self.admin,participant_id=self.person.pk,
            data={'activity_type':'email','summary':'SYNTHETIC unrelated record','occurred_at':timezone.now()},operation_id=uuid.uuid4())
        services.create_employer(actor=self.customer,organization_id=self.organization.pk,disclosure_id=self.grant.pk,
            data={'activity_type':'call','summary':'SYNTHETIC employer record','occurred_at':timezone.now()},operation_id=uuid.uuid4())
        r=self.client.get(self.profile,{'section':'relationship'})
        self.assertEqual(r.context['crm_summary']['btw_customer_count'],1)
        self.assertEqual(r.context['crm_summary']['employer_count'],1)
        staff=get_user_model().objects.create_user(username='synthetic-history-staff',is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=['view_participant','view_participantnote']))
        self.client.force_login(staff,backend='django.contrib.auth.backends.ModelBackend')
        r=self.client.get(self.profile,{'section':'relationship'})
        self.assertIsNone(r.context['crm_summary']['employer_count'])
        self.assertContains(r,'<strong>—</strong>')
        self.assertNotContains(r,'SYNTHETIC employer record')

    def test_source_event_comes_from_acquisition_not_later_registration(self):
        from datetime import timedelta
        from apps.events.models import Event, EventRegistration
        from .workspace_models import ParticipantAcquisition
        now=timezone.now()
        source=Event.objects.create(name='SYNTHETIC Original source',start_at=now-timedelta(days=30),end_at=now-timedelta(days=29))
        later=Event.objects.create(name='SYNTHETIC Later participation',start_at=now-timedelta(days=2),end_at=now-timedelta(days=1))
        ParticipantAcquisition.objects.filter(participant=self.person).update(channel='event',event=source)
        EventRegistration.objects.create(participant=self.person,event=later)
        r=self.client.get(self.profile)
        self.assertEqual(r.context['acquisition_record'].event,source)
        self.assertContains(r,source.name)
        self.assertContains(r,reverse('events:event_detail',args=[source.pk]))
        staff=get_user_model().objects.create_user(username='synthetic-source-reader',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_participant'))
        self.client.force_login(staff,backend='django.contrib.auth.backends.ModelBackend')
        r=self.client.get(self.profile)
        self.assertContains(r,source.name)
        self.assertNotContains(r,reverse('events:event_detail',args=[source.pk]))

    def test_actor_names_use_actual_linked_staff_names_and_honest_fallbacks(self):
        from apps.staffing.models import StaffProfile
        actor=get_user_model().objects.create_user(username='synthetic.username-is-not-a-name')
        self.assertEqual(compact_actor(actor),actor.username)
        StaffProfile.objects.create(user=actor,first_name='Jane',last_name='Doe')
        actor=get_user_model().objects.get(pk=actor.pk)
        self.assertEqual(compact_actor(actor),'Jane D.')
        self.assertEqual(compact_actor(None),'Unavailable account')

    def test_customer_overview_retains_authorized_summary_metrics(self):
        services.create_internal(actor=self.admin,organization_id=self.organization.pk,
            data={'activity_type':'call','summary':'SYNTHETIC Customer overview contact','occurred_at':timezone.now()},operation_id=uuid.uuid4())
        r=self.client.get(reverse('customers:workspace',args=[self.organization.pk,'overview']))
        self.assertTrue(r.context['crm_summary']['btw_allowed'])
        self.assertEqual(r.context['crm_summary']['btw_customer_count'],1)
        self.assertContains(r,'Last contact')
        self.assertContains(r,'Phone call')
        self.assertNotContains(r,'Contact context')
        relationship=self.client.get(reverse('customers:workspace',args=[self.organization.pk,'crm']))
        self.assertContains(relationship,'data-record-editor-dialog')

    def test_prior_explicit_clear_wins_without_rewriting_intake_and_newer_manual_maybe_wins(self):
        from apps.recruiting.models import RecruitingProfile
        from apps.core.audit import record_audit
        from apps.recruiting.preferences import current_preferences
        career=ParticipantCareerProfile.objects.get(participant=self.person)
        career.willing_to_travel=None
        career.intake_data={**career.intake_data,'travel_willingness':'no'};career.save()
        profile,_=RecruitingProfile.objects.get_or_create(participant=self.person)
        record_audit(actor=self.admin,action='recruiting.preferences.updated',instance=profile,
            source='build15_recruiting',new_data={'willing_to_travel':None},reason='SYNTHETIC prior explicit clear')
        before=career.intake_data.copy();count=AuditLog.objects.count()
        self.assertEqual(current_preferences(self.person)['travel'],'Not recorded')
        _,unknown,_=discover(self.admin,{'q':'#'+str(self.person.pk),'travel':'unknown'})
        _,negative,_=discover(self.admin,{'q':'#'+str(self.person.pk),'travel':'no'})
        self.assertEqual(unknown.count(),1);self.assertEqual(negative.count(),0)
        career.refresh_from_db();self.assertEqual(career.intake_data,before)
        self.assertEqual(AuditLog.objects.count(),count)
        data=self.card_data('preferences');self.assertEqual(data['travel'],'')
        data['travel']='maybe'
        self.assertEqual(self.client.post(self.card_url('preferences'),data,HTTP_X_BTW_EDITOR='1').status_code,200)
        self.assertEqual(current_preferences(self.person)['travel'],'Maybe')
        _,maybe,_=discover(self.admin,{'q':'#'+str(self.person.pk),'travel':'maybe'})
        self.assertEqual(maybe.count(),1)

    def test_existing_recruiting_form_supports_maybe_and_rejects_stale_typed_changes(self):
        from apps.recruiting import foundation
        from apps.recruiting.preferences import initial_preferences,current_preferences
        from apps.recruiting.forms import PreferencesForm
        original=list(self.person.registration_submissions.values('pk','answers','intake_metadata'))
        initial=initial_preferences(self.person)
        form=PreferencesForm(data={**{k:v if v is not None else '' for k,v in initial.items()},'willing_to_travel':'maybe','reason':'SYNTHETIC preference review'})
        self.assertTrue(form.is_valid(),form.errors)
        foundation.save_preferences(actor=self.admin,participant_id=self.person.pk,data=form.cleaned_data,
            expected_revision=initial['revision'],why=form.cleaned_data['reason'])
        self.assertEqual(current_preferences(self.person)['travel'],'Maybe')
        stale=initial_preferences(self.person)
        data=self.card_data('preferences');data['travel']=''
        self.client.post(self.card_url('preferences'),data,HTTP_X_BTW_EDITOR='1')
        with self.assertRaisesMessage(ValidationError,'profile changed'):
            foundation.save_preferences(actor=self.admin,participant_id=self.person.pk,
                data={**stale,'willing_to_travel':'maybe'},expected_revision=stale['revision'],why='SYNTHETIC stale form')
        self.assertEqual(current_preferences(self.person)['travel'],'Not recorded')
        self.assertEqual(list(self.person.registration_submissions.values('pk','answers','intake_metadata')),original)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, connections
from django.test import TransactionTestCase


@skipUnless(connection.vendor == 'postgresql', 'Row-lock race requires PostgreSQL')
class CardConcurrencyTests(EmployerFixtures, TransactionTestCase):
    def setUp(self):
        self.employer_setup()

    def test_one_stale_writer_is_rejected_without_lost_updates(self):
        self.person.refresh_from_db()
        form=ProfileCardForm(card='identity',person=self.person,actor=self.admin)
        data={key:form.initial.get(key) if form.initial.get(key) is not None else '' for key in form.fields}
        barrier=Barrier(2)
        def writer(city):
            try:
                barrier.wait(timeout=10)
                save_card(actor=self.admin,participant_id=self.person.pk,card='identity',data={**data,'city':city})
                return 'saved'
            except ValidationError:
                return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(writer,['SYNTHETIC writer one','SYNTHETIC writer two']))
        self.assertCountEqual(results,['saved','conflict'])
        self.person.refresh_from_db()
        self.assertIn(self.person.city,['SYNTHETIC writer one','SYNTHETIC writer two'])
