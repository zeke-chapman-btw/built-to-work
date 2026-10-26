"""Synthetic round-trip and permission coverage for existing Event fields only."""
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditLog
from apps.events.forms import EventForm
from apps.events.models import Event
from apps.operations.models import Task
from apps.operations.execution_models import EventExecution
from apps.operations.templatetags.event_ui import event_editor_sections
from apps.staffing.models import StaffingPlan


class FormStructure(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.details = {}
        self.stack = []
        self.fields = []
        self.links = []
        self.submits = []
        self.in_event_form = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            self.in_event_form = 'event-editor-form' in a.get('class', '').split()
        if tag == 'details':
            self.stack.append(a.get('id'))
            if a.get('id'):
                self.details[a['id']] = {'open': 'open' in a, 'fields': []}
        if tag in ('input', 'textarea', 'select') and a.get('name'):
            self.fields.append(a['name'])
            for key in self.stack:
                if key in self.details:
                    self.details[key]['fields'].append(a['name'])
        if tag == 'a':
            self.links.append(a.get('href', ''))
        if tag == 'button' and a.get('type') == 'submit' and self.in_event_form:
            self.submits.append(a)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.in_event_form = False
        if tag == 'details' and self.stack:
            self.stack.pop()


class EventContactHousingPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_superuser('SYNTHETIC-Fields-Owner', 'owner@example.test', None)
        cls.viewer = User.objects.create_user('SYNTHETIC-Fields-Viewer', is_staff=True)
        cls.manager = User.objects.create_user('SYNTHETIC-Fields-Staffing', is_staff=True)
        for user in (cls.viewer, cls.manager):
            user.user_permissions.add(Permission.objects.get(content_type__app_label='events', codename='view_event'))
        cls.manager.user_permissions.add(Permission.objects.get(content_type__app_label='staffing', codename='manage_event_staffing'))
        cls.values = dict(contact_name='SYNTHETIC <Contact>', contact_email='contact@example.test',
                          contact_phone='+1-555-0100', housing_information='SYNTHETIC lodging <b>Example</b>\nSecond line',
                          logistics_instructions='SYNTHETIC logistics & equipment\nKeep route clear')
        cls.event = Event.objects.create(name='SYNTHETIC existing-fields event', status='upcoming',
            start_at=datetime(2030, 7, 1, 13, tzinfo=UTC), end_at=datetime(2030, 7, 2, 21, tzinfo=UTC),
            timezone_name='America/New_York', **cls.values)
        cls.plan = StaffingPlan.objects.create(event=cls.event, required_staff=2, required_drivers=1,
            is_published=True, signup_deadline=datetime(2030, 6, 25, 19, tzinfo=UTC))

    def setUp(self):
        self.client.force_login(self.owner, backend='django.contrib.auth.backends.ModelBackend')

    def edit_url(self, event=None):
        return reverse('events:event_edit', args=[(event or self.event).pk])

    def detail_url(self, event=None):
        return reverse('events:event_detail', args=[(event or self.event).pk])

    def payload(self, event=None, **changes):
        event = event or self.event
        event.refresh_from_db()
        form = EventForm(instance=event)
        data = {k: '' if v is None else v for k, v in form.initial.items()}
        data.update(action='save', revision=event.updated_at.isoformat())
        data.update(changes)
        return data

    def structure(self, response):
        return FormStructure(response.content.decode())

    def test_actual_editor_partitions_existing_fields_without_duplicates(self):
        groups = {g['id']: [f.name for f in g['fields']] for g in event_editor_sections(EventForm(instance=self.event))}
        self.assertEqual(groups['event-contact-logistics'], ['contact_name', 'contact_email', 'contact_phone'])
        self.assertEqual(groups['event-housing'], ['housing_information'])
        self.assertEqual(groups['event-logistics'], ['logistics_instructions'])
        fields = [name for names in groups.values() for name in names]
        self.assertEqual(len(fields), len(set(fields)))
        self.assertEqual(set(fields), set(EventForm.Meta.fields))

    def test_blank_create_sections_are_optional_and_all_fields_remain_submittable(self):
        response = self.client.get(reverse('events:event_create'))
        self.assertEqual(response.status_code, 200)
        parsed = self.structure(response)
        for key in ('event-contact-logistics', 'event-housing', 'event-logistics'):
            self.assertFalse(parsed.details[key]['open'])
        for name in self.values:
            self.assertEqual(parsed.fields.count(name), 1)
        for title in ('Event Contact', 'Event Housing', 'Travel &amp; Logistics'):
            self.assertContains(response, title)
        self.assertContains(response, 'Save the event, then configure daily Staff &amp; Driver needs')
        for future in ('housing_type', 'hotel_name', 'housing_address', 'provision_status', 'receipt_url'):
            self.assertNotIn(future, parsed.fields)

    def test_create_blank_optional_fields_and_reopen(self):
        data = dict(name='SYNTHETIC blank optional fields', start_at='2031-07-01T09:00',
            end_at='2031-07-01T17:00', timezone_name='America/New_York', group_mode='disabled',
            group_label='Group', action='save_draft', **{key: '' for key in self.values})
        response = self.client.post(reverse('events:event_create'), data)
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(name=data['name'])
        self.assertEqual(event.status, 'draft')
        for key in self.values:
            self.assertEqual(getattr(event, key), '')
        parsed = self.structure(self.client.get(self.edit_url(event)))
        self.assertTrue(all(not parsed.details[k]['open'] for k in ('event-contact-logistics', 'event-housing', 'event-logistics')))

    def test_populated_edit_save_reopen_preserves_every_optional_value(self):
        response = self.client.get(self.edit_url())
        parsed = self.structure(response)
        self.assertTrue(all(parsed.details[k]['open'] for k in ('event-contact-logistics', 'event-housing', 'event-logistics')))
        response = self.client.post(self.edit_url(), self.payload(name='SYNTHETIC renamed only'))
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        for key, value in self.values.items():
            self.assertEqual(getattr(self.event, key), value)
        form = self.client.get(self.edit_url()).context['form']
        for key, value in self.values.items():
            self.assertEqual(form.initial[key], value)

    def test_explicitly_clearing_housing_does_not_erase_contact_or_logistics(self):
        self.assertEqual(self.client.post(self.edit_url(), self.payload(housing_information='')).status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.housing_information, '')
        for key in ('contact_name', 'contact_email', 'contact_phone', 'logistics_instructions'):
            self.assertEqual(getattr(self.event, key), self.values[key])

    def test_info_sections_escape_text_and_keep_contact_links_and_old_housing_anchor(self):
        response = self.client.get(self.detail_url())
        for title in ('Event Contact', 'Event Housing', 'Travel &amp; Logistics'):
            self.assertContains(response, title)
        for text in ('SYNTHETIC &lt;Contact&gt;', 'lodging &lt;b&gt;Example&lt;/b&gt;<br>Second line',
                     'logistics &amp; equipment<br>Keep route clear', 'mailto:contact@example.test', 'tel:+1-555-0100', 'id="housing-and-logistics"'):
            self.assertContains(response, text)
        links = self.structure(response).links
        self.assertTrue(any(x.endswith('#event-contact-logistics') for x in links))
        self.assertTrue(any(x.endswith('#event-housing') for x in links))
        self.assertTrue(any(x.endswith('#event-logistics') for x in links))
        self.assertNotContains(response, '<b>Example</b>')

    def test_invalid_contact_opens_its_section_and_retains_other_entries_without_a_write(self):
        before = AuditLog.objects.count()
        response = self.client.post(self.edit_url(), self.payload(contact_email='not-an-email'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('contact_email', response.context['form'].errors)
        self.assertTrue(self.structure(response).details['event-contact-logistics']['open'])
        self.assertContains(response, 'not-an-email', status_code=400)
        self.assertContains(response, 'Keep route clear', status_code=400)
        self.event.refresh_from_db()
        self.assertEqual(self.event.contact_email, self.values['contact_email'])
        self.assertEqual(AuditLog.objects.count(), before)

    def test_stale_save_keeps_attempted_values_and_cannot_overwrite_newer_data(self):
        stale = self.payload(contact_name='SYNTHETIC unsaved contact', housing_information='SYNTHETIC unsaved housing')
        latest = self.payload(logistics_instructions='SYNTHETIC newer logistics')
        self.assertEqual(self.client.post(self.edit_url(), latest).status_code, 302)
        before = AuditLog.objects.count()
        response = self.client.post(self.edit_url(), stale)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context['save_conflict'])
        self.assertContains(response, 'SYNTHETIC unsaved housing', status_code=400)
        self.assertContains(response, 'Review the latest version', status_code=400)
        self.assertTrue(self.structure(response).submits)
        self.assertTrue(all('disabled' in b for b in self.structure(response).submits))
        self.event.refresh_from_db()
        self.assertEqual(self.event.logistics_instructions, 'SYNTHETIC newer logistics')
        self.assertEqual(self.event.contact_name, self.values['contact_name'])
        self.assertEqual(AuditLog.objects.count(), before)

    def test_staff_summary_uses_existing_authority_and_event_local_deadline(self):
        response = self.client.get(self.detail_url())
        self.assertContains(response, 'Jun 25, 2030 · 3:00 PM')
        self.assertContains(response, 'Eastern (EDT)')
        edit = reverse('staffing:plan-edit', args=[self.event.pk])
        self.assertTrue(any(urlsplit(h).path == edit for h in self.structure(response).links))
        self.assertContains(response, 'Daily Staff &amp; Driver needs')

    def test_staffing_manager_without_event_edit_gets_only_staffing_actions(self):
        self.client.force_login(self.manager, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(self.detail_url())
        self.assertContains(response, reverse('staffing:plan-edit', args=[self.event.pk]))
        self.assertNotContains(response, self.edit_url())
        self.assertEqual(self.client.get(reverse('staffing:plan-edit', args=[self.event.pk])).status_code, 200)

    def test_viewer_cannot_see_staffing_deadline_or_gain_direct_editor_access(self):
        self.client.force_login(self.viewer, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(self.detail_url())
        self.assertNotContains(response, 'Jun 25, 2030 · 3:00 PM')
        self.assertNotContains(response, reverse('staffing:plan-edit', args=[self.event.pk]))
        self.assertEqual(self.client.get(reverse('staffing:plan-edit', args=[self.event.pk])).status_code, 403)
        self.assertEqual(self.client.get(self.edit_url()).status_code, 403)

    def test_unconfigured_plan_is_not_falsely_reported_as_no_deadline(self):
        event = Event.objects.create(name='SYNTHETIC no staffing plan', start_at=self.event.start_at,
            end_at=self.event.end_at, timezone_name=self.event.timezone_name)
        response = self.client.get(self.detail_url(event))
        self.assertContains(response, 'Staffing not configured')
        self.assertNotContains(response, 'No deadline set')
        self.assertFalse(StaffingPlan.objects.filter(event=event).exists())
        self.plan.signup_deadline = None
        self.plan.save(update_fields=['signup_deadline'])
        self.assertContains(self.client.get(self.detail_url()), 'No deadline set')

    def test_closed_staffing_keeps_history_link_but_has_no_mutating_editor_link(self):
        for status in ('completed', 'cancelled', 'archived'):
            self.event.status = status
            self.event.save(update_fields=['status'])
            response = self.client.get(self.detail_url())
            self.assertContains(response, reverse('staffing:event', args=[self.event.pk]))
            self.assertNotContains(response, reverse('staffing:plan-edit', args=[self.event.pk]))

    def test_staff_link_keeps_validated_return_context_and_read_gets_do_not_write(self):
        back = self.detail_url() + '?section=overview'
        before = (AuditLog.objects.count(), Task.objects.count(), EventExecution.objects.count(), StaffingPlan.objects.count(), self.event.updated_at)
        response = self.client.get(self.edit_url(), {'return_to': back})
        settings_path = reverse('staffing:plan-edit', args=[self.event.pk])
        link = next(h for h in self.structure(response).links if urlsplit(h).path == settings_path)
        self.assertEqual(parse_qs(urlsplit(link).query)['return_to'], [back])
        self.assertEqual(self.client.get(link).status_code, 200)
        self.client.get(reverse('events:event_create'))
        self.client.get(self.detail_url())
        self.event.refresh_from_db()
        self.assertEqual((AuditLog.objects.count(), Task.objects.count(), EventExecution.objects.count(), StaffingPlan.objects.count(), self.event.updated_at), before)
