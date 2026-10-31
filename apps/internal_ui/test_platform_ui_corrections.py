"""HTML document and protected navigation contracts, independent of CSS details."""
import re
import uuid
from types import SimpleNamespace
from django.template.loader import render_to_string
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from django.utils import timezone

class PortalDocumentCorrectionsTests(SimpleTestCase):
    def test_activity_empty_and_populated_title_does_not_include_history(self):
        event=SimpleNamespace(name='SYNTHETIC activity history',pk=uuid.uuid4())
        record=SimpleNamespace(event=event,started_at=timezone.now(),completed_at=None)
        for rows in ([],[record]):
            with self.subTest(populated=bool(rows)):
                body=render_to_string('participants/portal/activity.html',{'recorded_sessions':rows,'registrations':[],
                    'interests':[],'section':'activity'})
                self.assertEqual(re.search(r'<title>(.*?)</title>',body,re.S).group(1),'My Activity · Built to Work')
                self.assertEqual(body.count('<h2>Recorded participation</h2>'),1)
                if rows:self.assertEqual(body.count(event.name),1)

    def test_registered_event_card_retains_detail_and_registration_actions(self):
        event=SimpleNamespace(pk=uuid.uuid4(),name='SYNTHETIC registered event',start_at=timezone.now(),
            location_name='SYNTHETIC venue',city='',state='',event_type_label='Workforce event',my_registration=True,
            get_public_registration_status_display=lambda:'Open')
        body=render_to_string('participants/portal/events.html',{'events':[event]})
        self.assertIn(reverse('participants:portal-event',args=[event.pk]),body)
        self.assertIn('View Event Details',body)
        self.assertIn('View registration',body)

    def test_activity_does_not_link_to_no_longer_public_event(self):
        event=SimpleNamespace(name='SYNTHETIC historical event',pk=uuid.uuid4(),start_at=timezone.now(),location_name='Example venue')
        registration=SimpleNamespace(event=event,event_detail_available=False,is_past=True,portal_ticket=None,
                                     get_status_display=lambda:'Active')
        body=render_to_string('participants/portal/activity.html',{'registrations':[registration]})
        self.assertNotIn('View Event Details',body)
        registration.event_detail_available=True
        body=render_to_string('participants/portal/activity.html',{'registrations':[registration]})
        self.assertIn(reverse('participants:portal-event',args=[event.pk]),body)


class StaffTaskHeaderCorrectionsTests(TestCase):
    def test_new_task_header_uses_existing_quick_create_dialog(self):
        from django.contrib.auth import get_user_model
        from apps.accounts.roles import role_configuration
        from apps.staffing.models import StaffProfile
        role_configuration(apply=True)
        user=get_user_model().objects.create_superuser('synthetic-task-header','header@example.test','Synthetic-test-password-123')
        StaffProfile.objects.create(user=user)
        self.client.force_login(user,backend='django.contrib.auth.backends.ModelBackend')
        response=self.client.get('/operations/tasks/')
        self.assertEqual(response.status_code,200)
        header=re.search(r'<header class="page-header">(.*?)</header>',response.content.decode(),re.S).group(1)
        self.assertIn('New Task',header)
        self.assertIn('data-task-create',header)
        self.assertIn('/operations/tasks/quick-create/',header)
