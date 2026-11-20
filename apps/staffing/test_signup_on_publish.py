"""Publishing an event opens staff sign-up; managers can still close it."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.events.event_management import transition_event
from apps.events.models import Event
from apps.staffing.models import StaffingPlan


class SignupOpensOnPublishTests(TestCase):
    def test_publishing_opens_signup_once(self):
        admin = get_user_model().objects.create_superuser("pub-admin", "p@example.test", "x-test-only")
        start = timezone.now() + timedelta(days=10)
        event = Event.objects.create(name="SYNTHETIC publish", status="draft", start_at=start, end_at=start + timedelta(hours=6))
        saved, form = transition_event(event_id=event.pk, action="publish", actor=admin,
                                       data={"revision": event.updated_at.isoformat(), "confirm": "on"})
        self.assertIsNotNone(saved, form.errors if form else "")
        plan = StaffingPlan.objects.get(event=event)
        self.assertTrue(plan.is_published)
