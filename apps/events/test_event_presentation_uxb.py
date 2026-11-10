"""Synthetic regressions for owner-reviewed Event Info refinements."""
from datetime import UTC, datetime, time
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from apps.core.models import AuditLog
from .forms import EventForm
from .models import Event
from .timezone_choices import event_timezone_label, timezone_choices


class EventTimezonePresentationTests(SimpleTestCase):
    def event(self, zone, start, end=None):
        return SimpleNamespace(timezone_name=zone, start_at=start, end_at=end or start)

    def test_familiar_names_follow_daylight_saving(self):
        winter=datetime(2030,1,15,15,tzinfo=UTC)
        summer=datetime(2030,7,15,15,tzinfo=UTC)
        for zone, date, expected in [
            ('America/New_York',winter,'Eastern (EST)'),
            ('America/New_York',summer,'Eastern (EDT)'),
            ('America/Los_Angeles',winter,'Pacific (PST)'),
            ('America/Los_Angeles',summer,'Pacific (PDT)'),
            ('America/Phoenix',summer,'Arizona (MST)'),
            ('UTC',winter,'UTC'),
        ]:
            with self.subTest(zone=zone,date=date):
                self.assertEqual(event_timezone_label(self.event(zone,date)),expected)

    def test_cross_clock_change_and_deadline_use_their_own_dates(self):
        event=self.event('America/New_York',datetime(2030,11,2,15,tzinfo=UTC),datetime(2030,11,4,15,tzinfo=UTC))
        self.assertEqual(event_timezone_label(event),'Eastern (EDT / EST)')
        self.assertEqual(event_timezone_label(event,datetime(2030,11,4,12,tzinfo=UTC)),'Eastern (EST)')

    def test_additional_zones_and_submitted_values_remain_available(self):
        choices={key:label for _, group in timezone_choices('Invalid/Zone') for key,label in group}
        self.assertEqual(choices['America/New_York'],'Eastern (EST / EDT)')
        self.assertIn('Europe/London',choices)
        self.assertIn('Invalid/Zone',choices)
        data=dict(name='SYNTHETIC validation',start_at='2030-07-01T09:00',end_at='2030-07-01T17:00',timezone_name='Invalid/Zone',group_mode='disabled',group_label='Group')
        form=EventForm(data)
        self.assertFalse(form.is_valid())
        self.assertEqual(form['timezone_name'].value(),'Invalid/Zone')
        self.assertIn('Choose a valid time zone.',form.errors['timezone_name'])


class EventInfoPreservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=get_user_model().objects.create_superuser('synthetic-uxb-info','uxb-info@example.test','test-password')
        cls.event=Event.objects.create(name='SYNTHETIC Info review',code='SYNTHETIC-LEGACY-CODE',timezone_name='America/New_York',start_at=datetime(2030,7,1,13,tzinfo=UTC),end_at=datetime(2030,7,2,21,tzinfo=UTC),daily_start_time=time(9),daily_end_time=time(17))

    def setUp(self):
        self.client.force_login(self.user,backend='django.contrib.auth.backends.ModelBackend')

    def test_code_is_absent_from_staff_screens_but_survives_edit(self):
        event=self.event
        edit=reverse('events:event_edit',args=[event.pk])
        response=self.client.get(edit)
        self.assertNotContains(response,'name="code"')
        self.assertContains(response,'Eastern (EST / EDT)')
        data={key:('' if value is None else value) for key,value in EventForm(instance=event).initial.items()}
        data.update(name='SYNTHETIC edited Info review',action='save_draft',revision=event.updated_at.isoformat(),code='FORGED-CHANGE')
        self.assertEqual(self.client.post(edit,data).status_code,302)
        event.refresh_from_db()
        self.assertEqual(event.code,'SYNTHETIC-LEGACY-CODE')
        self.assertEqual((event.daily_start_time,event.daily_end_time),(time(9),time(17)))
        self.assertTrue(AuditLog.objects.filter(action='event.updated').exists())
        response=self.client.get(reverse('events:event_detail',args=[event.pk]))
        self.assertNotContains(response,'Event code')
        self.assertNotContains(response,'Daily planning hours')
        self.assertContains(response,'Eastern (EDT)')
        self.assertNotContains(self.client.get('/events/?view=all'),'SYNTHETIC-LEGACY-CODE')

    def test_invalid_edit_preserves_values_and_legacy_code(self):
        event=self.event
        data={key:('' if value is None else value) for key,value in EventForm(instance=event).initial.items()}
        data.update(name='SYNTHETIC unsaved',end_at='2030-06-30T17:00',action='save_draft',revision=event.updated_at.isoformat())
        response=self.client.post(reverse('events:event_edit',args=[event.pk]),data)
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'SYNTHETIC unsaved',status_code=400)
        self.assertContains(response,'Event end must be at or after event start.',status_code=400)
        event.refresh_from_db()
        self.assertEqual(event.name,'SYNTHETIC Info review')
        self.assertEqual(event.code,'SYNTHETIC-LEGACY-CODE')
