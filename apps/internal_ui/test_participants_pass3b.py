"""Synthetic Explorer integration, count, privacy and navigation regressions."""
import json
from decimal import Decimal
from urllib.parse import urlencode, urlsplit, parse_qs
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from apps.core.models import AuditLog
from apps.participants.models import Participant, ParticipantCareerProfile
from apps.participants.workspace_models import SavedParticipantView, ParticipantNote
from apps.participants.qualification_models import ParticipantQualification
from apps.participants.discovery import discover
from apps.participants.intelligence import build_intelligence
from apps.participants.identity import identity_context
from apps.internal_ui.people_editors import directory_origin, people_return


class ParticipantExplorerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser('synthetic-explorer', password='test-only-password')
        cls.reader = User.objects.create_user('synthetic-explorer-reader', is_staff=True)
        cls.analyst = User.objects.create_user('synthetic-explorer-analyst', is_staff=True)
        for user, names in [(cls.reader, ['view_participant']), (cls.analyst, ['view_participant', 'view_participant_analytics'])]:
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label='participants', codename__in=names))
        cls.people = []
        for name, state, interest, travel, years in [('Alpha','GA','seeking',True,5), ('Bravo','GA','seeking',False,0), ('Charlie','TX','open',True,None), ('Delta','GA','not_seeking',None,7), ('Echo','GA','',None,1)]:
            person = Participant.objects.create(first_name='SYNTHETIC '+name, last_name='Explorer', city='Example', state=state)
            ParticipantCareerProfile.objects.create(participant=person, employment_interest=interest, willing_to_travel=travel, years_of_experience=years, career_interests='Welding' if state=='GA' else 'Electrical')
            cls.people.append(person)
        ParticipantNote.objects.create(participant=cls.people[0], author=cls.admin, content='PRIVATE-NOTE-SENTINEL')
        for name in ['PRIVATE-QUALIFICATION-ONE','PRIVATE-QUALIFICATION-TWO']:
            ParticipantQualification.objects.create(participant=cls.people[0], custom_name=name, is_self_reported=True)
        cls.url = reverse('participants:intelligence')

    def setUp(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def response(self, **criteria):
        return self.client.get(self.url, criteria)

    def chart(self, response, dimension):
        return next(c for c in response.context['charts'] if c['dimension']==dimension)

    def test_default_group_and_native_controls(self):
        response=self.response()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['total_participants'],5)
        self.assertEqual(response.context['page_title'],'Participants')
        self.assertContains(response,'View matching participants')
        self.assertNotContains(response,'Must meet any criteria')
        self.assertNotContains(response,'Average Best Score')
        self.assertEqual(len(response.context['page_obj']),5)

    def test_single_then_multiple_and_criteria(self):
        first=self.response(employment='seeking')
        second=self.response(employment='seeking',travel='yes')
        self.assertEqual(first.context['total_participants'],2)
        self.assertEqual(second.context['total_participants'],1)
        self.assertEqual(second.context['page_obj'][0].pk,self.people[0].pk)
        travel=next(r for r in self.chart(second,'travel')['rows'] if r['bucket']=='yes')
        self.assertEqual(travel['count'],1)

    def test_remove_one_chip_preserves_other_criteria(self):
        response=self.response(employment='seeking',travel='yes',city='Example',sort='name_desc')
        chip=next(c for c in response.context['explorer_chips'] if c['label']=='Travel')
        query=parse_qs(urlsplit(chip['url']).query)
        self.assertNotIn('travel',query)
        self.assertEqual(query['employment'],['seeking'])
        self.assertEqual(query['city'],['Example'])
        self.assertEqual(query['sort'],['name_desc'])
        removed=self.client.get(chip['url'])
        self.assertEqual(removed.context['total_participants'],2)

    def test_clear_returns_active_default(self):
        response=self.response(travel='no')
        cleared=self.client.get(response.context['explorer_clear'])
        self.assertEqual(cleared.context['total_participants'],5)
        self.assertEqual(cleared.context['explorer_chips'],[])

    def test_invalid_and_empty_states_are_truthful(self):
        invalid=self.response(travel='invented')
        self.assertEqual(invalid.status_code,400)
        self.assertEqual(invalid.context['total_participants'],0)
        empty=self.response(q='NO-MATCH-SYNTHETIC')
        self.assertEqual(empty.context['total_participants'],0)
        self.assertContains(empty,'No matching participants')
        self.assertContains(empty,'No current group to summarize')
        self.assertTrue(all(r['count']==0 for c in empty.context['charts'] for r in c['rows']))

    def test_unknown_no_and_zero_are_different(self):
        no=self.response(travel='no')
        unknown=self.response(travel='unknown')
        self.assertEqual([p.pk for p in no.context['page_obj']],[self.people[1].pk])
        self.assertEqual({p.pk for p in unknown.context['page_obj']},{self.people[3].pk,self.people[4].pk})
        zero=self.response(experience_min='0')
        self.assertEqual(zero.context['total_participants'],4)
        self.assertIn('experience_min',zero.context['criteria'])

    def test_primary_charts_stay_inside_selected_facet(self):
        criteria={'segments':json.dumps([['state','ga']])}
        response=self.client.get(self.url,criteria)
        self.assertEqual(response.context['total_participants'],4)
        self.assertEqual([(r['label'],r['count']) for r in self.chart(response,'state')['rows']],[('GA',4)])
        self.assertTrue(all(r['url'] is None for c in response.context['charts'] for r in c['rows']))

    def test_available_alternatives_retain_existing_facet_semantics(self):
        response=self.client.get(self.url,{'segments':json.dumps([['state','ga']]),'view':'alternatives'})
        counts={r['label']:r['count'] for r in self.chart(response,'state')['rows']}
        self.assertEqual(counts,{'GA':4,'TX':1})
        self.assertEqual(response.context['total_participants'],4)
        self.assertContains(response,'not current-group totals')
        for row in self.chart(response,'state')['rows']:
            result=self.client.get(row['url'])
            self.assertEqual(result.context['total_participants'],row['count'])

    def test_graphical_partition_and_overlapping_evidence_are_distinct(self):
        response=self.response()
        charts=response.context['explorer_charts']
        self.assertTrue(next(c for c in charts if c['dimension']=='channel')['gradient'])
        qualification=next(c for c in charts if c['dimension']=='qualification')
        self.assertNotIn('gradient',qualification)
        unverified=next(r for r in self.chart(response,'verification')['rows'] if r['bucket']=='unverified')
        self.assertEqual(unverified['count'],1)

    def test_profile_and_specialist_origin_preserves_explorer(self):
        origin=self.url+'?'+urlencode({'travel':'yes','city':'Example','sort':'name_desc','view':'details'})
        profile=reverse('participants:staff-profile',args=[self.people[0].pk])
        response=self.client.get(profile,{'from':origin})
        self.assertEqual(response.context['directory_origin'],origin)
        self.assertContains(response,'profile-assessments')
        request=RequestFactory().get(profile,{'from':origin})
        self.assertEqual(directory_origin(request),origin)
        nested=RequestFactory().get(profile,{'return_to':profile+'?'+urlencode({'from':origin})})
        self.assertEqual(directory_origin(nested),origin)

    def test_return_guard_rejects_external_or_unknown_parameters(self):
        for origin in ['https://example.invalid/','//example.invalid/',self.url+'?unexpected=1']:
            request=RequestFactory().get('/participants/staff/directory/1/',{'from':origin})
            self.assertEqual(directory_origin(request),reverse('participants:staff-directory'))

    def test_saved_views_are_owner_private_and_rechecked(self):
        saved=SavedParticipantView.objects.create(owner=self.admin,name='SYNTHETIC private',criteria={'travel':'yes'})
        response=self.response(saved=str(saved.pk))
        self.assertEqual(response.context['total_participants'],2)
        self.client.force_login(self.analyst, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.response(saved=str(saved.pk)).status_code,404)
        guarded=SavedParticipantView.objects.create(owner=self.analyst,name='SYNTHETIC revoked',criteria={'qualification_status':'verified'})
        self.assertEqual(self.response(saved=str(guarded.pk)).status_code,403)
        saved.schema_version=99;saved.save()
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.response(saved=str(saved.pk)).status_code,400)

    def test_analytics_gate_and_hidden_evidence_domains(self):
        self.client.force_login(self.reader, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.response().status_code,403)
        self.client.force_login(self.analyst, backend='django.contrib.auth.backends.ModelBackend')
        response=self.response()
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'PRIVATE-NOTE-SENTINEL')
        self.assertNotContains(response,'PRIVATE-QUALIFICATION')
        self.assertNotContains(response,'name="qualification"')
        self.assertNotContains(response,'name="document_status"')
        self.assertNotContains(response,'Accepted assessment records')
        for criteria in [{'qualification_status':'verified'},{'document_status':'present'},{'assessment':'simulator'}]:
            self.assertEqual(self.client.get(self.url,criteria).status_code,403)

    def test_discrete_type_changes_clear_only_incompatible_score_fields(self):
        response=self.response(assessment='quiz',score_min='0',city='Example')
        group=next(g for g in response.context['explorer_groups'] if g['title']=='Assessment source')
        target=next(o['url'] for o in group['options'] if parse_qs(urlsplit(o['url']).query).get('assessment')==['simulator'])
        query=parse_qs(urlsplit(target).query)
        self.assertEqual(query['city'],['Example'])
        self.assertEqual(query['assessment'],['simulator'])
        self.assertNotIn('score_min',query)

    def test_sorting_pagination_and_archive_default(self):
        from django.utils import timezone
        self.people[4].archived_at=timezone.now();self.people[4].save()
        self.assertEqual(self.response().context['total_participants'],4)
        self.assertEqual(self.response(archived='only').context['total_participants'],1)
        self.assertEqual(self.response(archived='all').context['total_participants'],5)
        self.assertEqual(self.response(sort='name_desc').context['page_obj'][0].pk,self.people[3].pk)

    def test_gets_do_not_mutate_domain_or_audit_records(self):
        before=(list(Participant.objects.values()),list(ParticipantCareerProfile.objects.values()),AuditLog.objects.count(),ParticipantNote.objects.count(),ParticipantQualification.objects.count())
        for criteria in [{},{'travel':'yes'},{'view':'alternatives'},{'view':'details'},{'q':'none'}]:
            self.assertEqual(self.client.get(self.url,criteria).status_code,200)
        after=(list(Participant.objects.values()),list(ParticipantCareerProfile.objects.values()),AuditLog.objects.count(),ParticipantNote.objects.count(),ParticipantQualification.objects.count())
        self.assertEqual(before,after)

    def test_ids_and_counts_use_canonical_read_projection(self):
        alias=Participant.objects.create(first_name='SYNTHETIC Alias',last_name='Explorer')
        with patch('apps.participants.identity.resolved_identity_map',return_value={alias.pk:self.people[0].pk}):
            response=self.response()
            self.assertEqual(response.context['total_participants'],5)
            self.assertNotIn(alias.pk,[p.pk for p in response.context['page_obj']])

    def test_query_growth_is_page_bounded(self):
        with CaptureQueriesContext(connection) as small:
            self.response()
        Participant.objects.bulk_create([Participant(first_name=f'SYNTHETIC Bulk {i}',last_name='Explorer',state='GA') for i in range(80)])
        with CaptureQueriesContext(connection) as large:
            response=self.response()
        self.assertEqual(response.context['total_participants'],85)
        self.assertEqual(len(response.context['page_obj']),25)
        self.assertLessEqual(len(large),len(small)+12)
        self.assertNotContains(response,'SYNTHETIC Bulk 79')

    def test_field_ids_are_unique_across_desktop_mobile_forms(self):
        from html.parser import HTMLParser
        class Fields(HTMLParser):
            def __init__(self):super().__init__();self.ids=[]
            def handle_starttag(self,tag,attrs):
                values=dict(attrs)
                if 'id' in values:self.ids.append(values['id'])
        parser=Fields();parser.feed(self.response().content.decode())
        self.assertEqual(len(parser.ids),len(set(parser.ids)))

    def test_accepted_units_and_void_exclusion_are_preserved(self):
        from apps.participants import test_workspace_11 as fixtures
        fixture=fixtures.AcceptedEvidence11Tests()
        fixture.setUp()
        fixture.quiz(correct=1)
        fixture.quiz(mode='demo',correct=2)
        void=fixture.quiz(correct=2)
        from django.utils import timezone
        void.status='void';void.voided_at=timezone.now();void.save()
        response=self.response()
        quiz=next(row for row in response.context['assessment_rows'] if row['label']=='Quiz')
        self.assertEqual((quiz['count'],quiz['people'],quiz['average']),(1,1,Decimal('1')))
        self.assertEqual(quiz['unit'],'correct answers')
        self.assertEqual({r['unit'] for r in response.context['assessment_rows']},{'correct answers','percent','raw points'})


    def test_main_directory_is_unified_without_analytics_permission(self):
        self.client.force_login(self.reader, backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('participants:staff-directory'), {'employment':'seeking','travel':'yes'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['total_participants'],1)
        self.assertEqual(response.context['active_participant_tab'],'directory')
        self.assertContains(response,'Explore / Filter')
        self.assertContains(response,'p3-directory-row')
        self.assertContains(response,'Seeking work')
        self.assertContains(response,'Travel: Yes')
        self.assertNotContains(response,'Current group analysis')
        self.assertNotContains(response,'PRIVATE-QUALIFICATION')
        self.assertFalse(response.context['explorer_include_analysis'])

    def test_main_directory_offers_existing_add_and_opt_in_analysis(self):
        response=self.client.get(reverse('participants:staff-directory'), {'state':'GA'})
        self.assertContains(response,'+ Add participant')
        self.assertNotContains(response,'aria-label="Participant workspace"')
        self.assertContains(response,'Existing staff workflows')
        target=response.context['explorer_analysis_url']
        result=self.client.get(target)
        self.assertEqual(result.context['total_participants'],4)
        self.assertTrue(result.context['explorer_include_analysis'])
        self.assertEqual(result.context['page_title'],'Participants')

    def test_compact_toolbar_retains_private_views_and_single_analysis_entry(self):
        response=self.client.get(reverse('participants:staff-directory'))
        self.assertContains(response,'data-ui-menu')
        self.assertContains(response,'Save current view')
        self.assertContains(response,'Saved views')
        self.assertContains(response,'>Analysis</a>',count=1)
        self.assertNotContains(response,'>View analysis</a>')
        self.assertContains(response,'View matching participants')

    def test_common_filters_apply_with_existing_discrete_criteria(self):
        response=self.client.get(reverse('participants:staff-directory'),{'employment':'seeking','state':'GA','travel':'yes'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['total_participants'],1)
        self.assertEqual([field['label'] for field in response.context['explorer_common']['desktop']],['Trade / work interest','City','State'])
        hidden=dict(response.context['explorer_filter_hidden'])
        self.assertEqual(hidden['employment'],'seeking')
        self.assertEqual(hidden['travel'],'yes')
        chip=next(row for row in response.context['explorer_chips'] if row['label']=='Travel')
        self.assertEqual(chip['value'],'Yes')

    def test_invalid_common_filter_has_linked_error_and_keeps_zero_result_state(self):
        response=self.client.get(reverse('participants:staff-directory'),{'experience_min':'invalid'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.context['total_participants'],0)
        self.assertContains(response,'aria-invalid="true"',status_code=400)
        self.assertContains(response,'id="explorer-desktop-experience_min-errors"',status_code=400)

    def test_simplified_profile_retains_unique_tools_and_filtered_return(self):
        origin=reverse('participants:staff-directory')+'?employment=seeking'
        response=self.client.get(reverse('participants:staff-profile',args=[self.people[0].pk]),{'from':origin})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['directory_origin'],origin)
        self.assertNotContains(response,'Detailed management')
        self.assertNotContains(response,'Jump to section')
        self.assertContains(response,'Linked tasks')
        self.assertContains(response,'Participant portal invitation')
        self.assertContains(response,'Add internal note')

    def test_profile_simplification_preserves_restricted_evidence_boundaries(self):
        self.client.force_login(self.reader,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get(reverse('participants:staff-profile',args=[self.people[0].pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Linked tasks')
        self.assertNotContains(response,'Participant portal invitation')
        self.assertNotContains(response,'PRIVATE-NOTE-SENTINEL')
        self.assertNotContains(response,'PRIVATE-QUALIFICATION')
        self.assertNotContains(response,'Add internal note')
        self.assertNotContains(response,'Download securely')
