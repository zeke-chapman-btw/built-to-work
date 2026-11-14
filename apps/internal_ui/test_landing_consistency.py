"""Landing composition must preserve real views, GET filters and editor boundaries."""
from html.parser import HTMLParser
from django.urls import reverse
from apps.reporting.test_metrics import ReportingFixtures


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'parents': self.stack.copy()}
        self.nodes.append(node)
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def marked(self, marker):
        return [n for n in self.nodes if marker in n['attrs']]


class LandingConsistencyTests(ReportingFixtures):
    routes = [
        ('/operations/tasks/', 'Tasks', 'filters'),
        ('/internal/workspaces/participants/', 'Participants', 'none'),
        ('/internal/customers/', 'Customers', 'none'),
        ('/recruiting/', 'Recruiting', 'sections'),
        ('/internal/workspaces/approvals/', 'Approvals', 'sections'),
        ('/events/', 'Events', 'none'),
        ('/internal/workspaces/operations/', 'Operations', 'sections'),
        ('/internal/reports/', 'Reports &amp; Analytics', 'sections'),
        ('/internal/settings/', 'Settings', 'sections'),
    ]

    def setUp(self):
        self.client.force_login(self.admin, backend='django.contrib.auth.backends.ModelBackend')

    def landing(self, route):
        response = self.client.get(route)
        self.assertEqual(response.status_code, 200, route)
        return response, Document(response.content.decode())

    def test_real_landing_views_have_one_composed_header_and_results_frame(self):
        for route, title, kind in self.routes:
            with self.subTest(route=route):
                response, doc = self.landing(route)
                frame, = doc.marked('data-workspace-landing')
                header, = doc.marked('data-workspace-header')
                content, = doc.marked('data-workspace-content')
                self.assertIn(frame, header['parents'])
                self.assertIn(frame, content['parents'])
                headings = [n for n in doc.nodes if n['tag'] == 'h1' and frame in n['parents']]
                self.assertEqual(len(headings), 1)
                self.assertIn(header, headings[0]['parents'])
                self.assertContains(response, title)
                self.assertContains(response, 'shared/workspace_landing.css', count=1)
                for n in doc.nodes:
                    if n['tag'] in {'a','button'} and header in n['parents']:
                        self.assertFalse(any(p['tag'] == 'form' for p in n['parents']))

    def test_route_sections_and_filter_choices_keep_distinct_semantics(self):
        for route, title, kind in self.routes:
            with self.subTest(route=route):
                _, doc = self.landing(route)
                selections = doc.marked('data-workspace-selection')
                actual = [n for n in selections if n['attrs']['data-workspace-selection'] != 'none']
                self.assertEqual(len(actual), 0 if kind == 'none' else 1)
                if actual:
                    self.assertEqual(actual[0]['attrs']['data-workspace-selection'], kind)
                    links = [n for n in doc.nodes if n['tag']=='a' and actual[0] in n['parents']]
                    self.assertTrue(links)
                    self.assertTrue(any(n['attrs'].get('aria-current') for n in links))
                    self.assertFalse(any(n['attrs'].get('role') == 'tab' for n in links))

    def test_task_controller_contains_choices_filters_and_results_in_that_order(self):
        _, doc = self.landing('/operations/tasks/?view=all&q=synthetic')
        frame, = doc.marked('data-task-workspace')
        selection, = [n for n in doc.marked('data-workspace-selection') if n['attrs']['data-workspace-selection']=='filters']
        filters, = doc.marked('data-workspace-filters')
        content, = doc.marked('data-workspace-content')
        self.assertIn(frame, filters['parents'])
        self.assertLess(doc.nodes.index(selection), doc.nodes.index(filters))
        self.assertLess(doc.nodes.index(filters), doc.nodes.index(content))
        query, = [n for n in doc.nodes if n['tag']=='input' and n['attrs'].get('name')=='q']
        self.assertEqual(query['attrs'].get('value'), 'synthetic')

    def test_directory_controller_still_contains_search_and_extended_filters(self):
        _, doc = self.landing('/internal/workspaces/participants/?q=synthetic&sort=name_desc')
        frame, = [n for n in doc.nodes if n['attrs'].get('id')=='people-directory']
        filters, = doc.marked('data-workspace-filters')
        self.assertIn(frame, filters['parents'])
        fields = [n for n in doc.nodes if n['tag'] in {'input','select'} and filters in n['parents']]
        self.assertGreater(len(fields), 30)
        self.assertTrue(any(n['attrs'].get('name')=='q' and n['attrs'].get('value')=='synthetic' for n in fields))

    def test_event_creation_is_independent_of_preserved_get_filter_form(self):
        _, doc = self.landing('/events/?q=synthetic')
        header, = doc.marked('data-workspace-header')
        filters, = doc.marked('data-workspace-filters')
        forms = [n for n in doc.nodes if n['tag']=='form' and filters in n['parents']]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]['attrs'].get('method','get').lower(),'get')
        fields = {n['attrs'].get('name') for n in doc.nodes if forms[0] in n['parents']}
        self.assertTrue({'q','view','event_type','date_from','date_to'} <= fields)
        self.assertTrue(any(n['tag']=='a' and header in n['parents'] for n in doc.nodes))

    def test_reporting_section_selection_preserves_region_and_filter_values(self):
        response, doc = self.landing('/internal/reports/?tab=events&region=GA')
        selection, = [n for n in doc.marked('data-workspace-selection') if n['attrs']['data-workspace-selection']=='sections']
        selected, = [n for n in doc.nodes if n['tag']=='a' and selection in n['parents'] and n['attrs'].get('aria-current')]
        self.assertIn('tab=events', selected['attrs']['href'])
        self.assertContains(response, 'region=GA')
        self.assertTrue(any(n['attrs'].get('name')=='region' and n['attrs'].get('value')=='GA' for n in doc.nodes))

    def test_worker_settings_does_not_gain_administrator_sections(self):
        self.client.force_login(self.worker, backend='django.contrib.auth.backends.ModelBackend')
        response, _ = self.landing('/internal/settings/?tab=advanced')
        self.assertEqual(response.context['settings_section'],'account')
        for key in ['staff','portal','compensation','recruiting','advanced']:
            self.assertNotContains(response, 'data-settings-tab="'+key+'"')

    def test_event_report_editor_keeps_its_existing_frame_and_assets(self):
        response = self.client.get(reverse('reporting:event', args=[self.event.pk]))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'shared/workspace_landing.css')
        self.assertNotContains(response,'data-workspace-landing')
