"""Synthetic Pass 1 contracts: navigation policy, disclosure and event context."""
from datetime import UTC, datetime
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.events.forms import EventForm
from apps.events.models import Event
from apps.internal_ui.navigation import staff_destinations as visible_workspaces, visible_legacy_workspaces
from apps.operations.templatetags.event_ui import event_editor_sections


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nested = False

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.nested |= self.depth > 0
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == 'a':
            self.depth -= 1


class CorrectivePassOneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser('synthetic-p1-owner', 'p1@example.test', 'test-password')
        cls.viewer = get_user_model().objects.create_user('synthetic-p1-viewer', is_staff=True)
        cls.viewer.user_permissions.add(Permission.objects.get(codename='view_event', content_type__app_label='events'))
        cls.event = Event.objects.create(name='SYNTHETIC P1', start_at=datetime(2030,7,1,13,tzinfo=UTC), end_at=datetime(2030,7,2,21,tzinfo=UTC), timezone_name='America/New_York')

    def setUp(self):
        self.client.force_login(self.owner, backend='django.contrib.auth.backends.ModelBackend')

    def test_seven_destinations_and_legacy_authorization(self):
        self.assertEqual([row['label'] for row in visible_workspaces(self.owner)], ['Home','Events','Participants','Operations','Tasks','Reports & Analytics','Settings & Administration'])
        self.assertIn('administration', [row['id'] for row in visible_legacy_workspaces(self.owner)])
        for key in ('administration','platform-tools','customers'):
            self.assertEqual(self.client.get(reverse('internal:workspace', kwargs={'workspace_id':key})).status_code, 200)
        self.client.force_login(self.viewer, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('internal:workspace', kwargs={'workspace_id':'administration'})).status_code,403)
        response=self.client.get(reverse('internal:settings'))
        self.assertNotContains(response, 'Expense Categories')
        self.assertNotContains(response, 'Django administration')

    def test_one_active_destination_and_event_expense_context(self):
        for path, expected in [('/events/', 'events'), ('/internal/reports/','reports'), ('/reports/categories/','settings'), ('/reports/events/123/','events'), ('/internal/settings/','settings'), ('/events/operations/','settings'), ('/operations/tasks/','tasks')]:
            with self.subTest(path=path):
                self.assertEqual([row['id'] for row in visible_workspaces(self.owner,path) if row['active']], [expected])

    def test_primary_record_has_no_nested_links(self):
        response=self.client.get('/events/?view=all&q=SYNTHETIC')
        self.assertContains(response,'btw-record-primary')
        parser=LinkParser();parser.feed(response.content.decode())
        self.assertFalse(parser.nested)
        self.assertContains(response,'View daily coverage')

    def test_setup_is_in_operations_and_results_keep_bridge(self):
        url=reverse('events:event_detail',args=[self.event.pk])
        operations=self.client.get(url, {'section':'operations'})
        self.assertContains(operations, 'Station assignments')
        self.assertContains(operations, 'class="station-assign-form"')
        results=self.client.get(url, {'section':'results'})
        self.assertNotContains(results, 'class="station-assign-form"')
        self.assertContains(results, 'Participant experience progress')
        self.assertContains(results, '?section=operations#event-stations')
        self.client.force_login(self.viewer, backend='django.contrib.auth.backends.ModelBackend')
        self.assertNotContains(self.client.get(url,{'section':'operations'}), 'class="station-assign-form"')

    def test_invalid_station_returns_to_same_event_without_write(self):
        url=reverse('events:station_assign',args=[self.event.pk])
        count=self.event.station_assignments.count()
        response=self.client.post(url, {'station':'invalid','enabled':'on','display_order':'0'})
        self.assertRedirects(response, reverse('events:event_detail',args=[self.event.pk])+'?section=operations#event-stations',fetch_redirect_response=False)
        self.assertEqual(self.event.station_assignments.count(),count)

    def test_all_original_fields_once_and_optional_error_opens(self):
        data=dict(name='SYNTHETIC retained',timezone_name='America/New_York',start_at='2030-07-01T09:00',end_at='2030-07-01T17:00',group_mode='disabled',group_label='Group',contact_email='invalid')
        form=EventForm(data);self.assertFalse(form.is_valid())
        groups=event_editor_sections(form)
        self.assertCountEqual([field.name for group in groups for field in group['fields']],list(EventForm.Meta.fields))
        self.assertTrue(next(group for group in groups if group['id']=='event-contact-logistics')['open'])
        response=self.client.post(reverse('events:event_create'),dict(data,action='save_draft'))
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'SYNTHETIC retained',status_code=400)
        self.assertContains(response,'id="event-contact-logistics" class="card btw-event-form-section" open',status_code=400)

    def test_housing_empty_and_filled_use_lined_facts(self):
        url=reverse('events:event_detail',args=[self.event.pk])
        self.assertContains(self.client.get(url),'Accommodation')
        self.event.housing_information='SYNTHETIC accommodation\nSecond line'
        self.event.save()
        response=self.client.get(url)
        self.assertContains(response,'SYNTHETIC accommodation<br>Second line')
        self.assertContains(response,'#event-contact-logistics')

    def test_foundation_is_opt_in_and_expense_editor_unchanged(self):
        self.assertContains(self.client.get(reverse('events:event_detail',args=[self.event.pk])),'has-corrective-events')
        self.assertNotContains(self.client.get(reverse('internal:settings')),'has-corrective-events')
        self.assertNotContains(self.client.get(reverse('internal:home')),'has-corrective-events')

    def test_empty_directory_distinguishes_no_records_from_no_matches(self):
        self.assertContains(self.client.get('/events/?view=all&q=NONMATCHING-SYNTHETIC'),'No events match this view')
        Event.objects.all().delete()
        self.assertContains(self.client.get('/events/?view=all'),'No events yet')

    def test_existing_simulator_references_keep_permission_guard(self):
        from apps.internal_ui.event_presentation import event_tools
        owner_links=event_tools(self.owner,self.event)
        config=next(row for row in owner_links if row['label']=='Simulator configuration references')
        self.assertEqual(config['url'],reverse('events:event_detail',args=[self.event.pk])+'?section=operations#event-configuration')
        self.assertEqual(self.client.get(config['url']).status_code,200)
        self.assertFalse(any(row['label']=='Simulator configuration references' for row in event_tools(self.viewer,self.event)))

    def test_coverage_sibling_records_its_own_event_return_context(self):
        response=self.client.get('/events/?view=all&q=SYNTHETIC')
        self.assertContains(response,'data-event-open="'+str(self.event.pk)+'" href="'+reverse('staffing:event',args=[self.event.pk])+'"')
