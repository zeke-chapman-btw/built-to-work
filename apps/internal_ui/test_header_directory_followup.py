"""Real-view contracts for consistent headings and the simplified directory."""
import re
from urllib.parse import parse_qs, urlsplit
from django.test import RequestFactory
from django.urls import reverse
from apps.reporting.test_metrics import ReportingFixtures
from apps.participants.models import Participant, ParticipantCareerProfile
from apps.participants.discovery import discover
from apps.participants.workspace_views import _criteria_from_request
from apps.participants.identity import identity_context
from . import test_landing_consistency as landing


class HeaderDirectoryFollowupTests(ReportingFixtures):
    def setUp(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')
        self.person = Participant.objects.create(first_name='SYNTHETIC Taylor', last_name='Directory', city='New York', state='NY')
        self.other = Participant.objects.create(first_name='SYNTHETIC Morgan', last_name='Directory', city='York', state='PA')
        ParticipantCareerProfile.objects.create(participant=self.person, employment_interest='seeking', willing_to_travel=False, career_interests='SYNTHETIC_HIDDEN_TRADE', skills_interests='SYNTHETIC_HIDDEN_SKILLS')
        self.route = reverse('participants:staff-directory')

    def test_all_main_header_identity_blocks_contain_only_the_title(self):
        for route, _, _ in landing.LandingConsistencyTests.routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                doc = landing.Document(response.content.decode())
                identity, = [n for n in doc.nodes if 'btw-landing-identity' in n['attrs'].get('class', '').split()]
                children = [n for n in doc.nodes if n['parents'] and n['parents'][-1] is identity]
                self.assertEqual([n['tag'] for n in children], ['h1'])

    def test_approvals_explanation_remains_in_the_results_content(self):
        response = self.client.get('/internal/workspaces/approvals/')
        description = response.context.get('description')
        self.assertTrue(description)
        self.assertContains(response, description)
        doc = landing.Document(response.content.decode())
        content, = doc.marked('data-workspace-content')
        explanation, = [n for n in doc.nodes if 'approval-help' in n['attrs'].get('class', '').split()]
        self.assertIn(content, explanation['parents'])

    def test_questions_are_selectors_with_no_duplicate_facet_hidden_inputs(self):
        response = self.client.get(self.route, {'employment':'seeking', 'travel':'no', 'location':'New York, NY'})
        self.assertEqual(response.status_code, 200)
        doc = landing.Document(response.content.decode())
        forms = doc.marked('data-participant-query')
        self.assertEqual(len(forms), 1)
        for form in forms:
            for key in ['employment','travel','channel','assessment','archived']:
                controls = [n for n in doc.nodes if form in n['parents'] and n['tag'] in ['select','input'] and n['attrs'].get('name')==key]
                # Seeking work / Will travel are chip links now; the chosen value rides along as one hidden input.
                self.assertEqual([n['tag'] for n in controls], ['input'] if key in ('employment','travel') else ['select'], key)
            form_links=[n for n in doc.nodes if n['tag']=='a' and form in n['parents']]
            self.assertIn(response.context['explorer_reset'],[n['attrs']['href'] for n in form_links])
            self.assertContains(response,'Save current search')
            self.assertContains(response,'Saved searches')
            fields = [n for n in doc.nodes if form in n['parents'] and n['tag']=='input' and n['attrs'].get('type')!='hidden']
            self.assertTrue(any(n['attrs'].get('name')=='location' for n in fields))
            self.assertFalse(any(n['attrs'].get('name') in ['trade_interest','city','state'] for n in fields))
        ids = [n['attrs']['id'] for n in doc.nodes if n['attrs'].get('id')]
        self.assertEqual(len(ids), len(set(ids)))

    def test_combined_location_matches_city_state_and_requires_every_term(self):
        with identity_context():
            for location, expected in [('new york, ny', {self.person.pk}), ('york', {self.person.pk,self.other.pk}), ('PA',{self.other.pk}), ('New York, PA',set())]:
                form, population, criteria = discover(self.admin, {'location':location})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(set(population.filter(pk__in=[self.person.pk,self.other.pk]).values_list('pk',flat=True)), expected)
                self.assertEqual(criteria['location'], location)

    def test_rows_show_identity_and_preferences_without_trade_skills_or_details(self):
        response = self.client.get(self.route, {'q':'SYNTHETIC Taylor', 'location':'NY', 'sort':'name_desc', 'assessment_source':''})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        article, = re.findall(r'<article class="ex-result ex-directory-record.*?</article>', html, re.S)
        self.assertNotIn(str(self.person.identity_uuid), article)
        self.assertIn('ex-person-id', article)
        self.assertIn('Seeking work', article)
        self.assertIn('>No</span>', article)
        # Owner design (Nov 2026) adds a Trade interest column; skills stay off the row.
        self.assertIn('SYNTHETIC_HIDDEN_TRADE', article)
        for unwanted in ['SYNTHETIC_HIDDEN_SKILLS','<details']:
            self.assertNotIn(unwanted, article)
        doc = landing.Document(article)
        links = [n for n in doc.nodes if n['tag']=='a']
        self.assertEqual(len(links), 1)
        origin = parse_qs(urlsplit(links[0]['attrs']['href']).query)['from'][0]
        self.assertIn('location=NY', origin)
        self.assertIn('sort=name_desc', origin)
        self.assertNotIn('assessment_source', origin)
        profile = self.client.get(links[0]['attrs']['href'])
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.context['directory_origin'], origin)

    def test_assessment_change_clears_limits_but_unchanged_source_preserves_them(self):
        factory=RequestFactory()
        for source, expected in [('simulator',False), ('quiz',True), ('',False)]:
            request=factory.get(self.route, {'assessment_source':'quiz','assessment':source,'score_min':'2','score_max':'4','assessment_category':'1','location':'NY'})
            values=_criteria_from_request(request)
            self.assertEqual('score_min' in values,expected)
            self.assertEqual('score_max' in values,expected)
            self.assertEqual('assessment_category' in values,expected)
            self.assertNotIn('assessment_source',values)
            self.assertEqual(values['location'],'NY')
