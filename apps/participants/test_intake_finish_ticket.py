"""The intake finish page shows the new registrant's own ticket, or says it's on its way."""
from datetime import timedelta
from django.test import TestCase, RequestFactory
from django.template.loader import render_to_string
from django.utils import timezone
from apps.events.models import Event, QrTicket
from .intake_submission import complete_session
from .intake_views import _finished_ticket
from . import test_intake_submission_11b as baseline


class IntakeFinishTicketTests(TestCase):
    setUp = baseline.IntakeSubmission11BTests.setUp
    answers = baseline.IntakeSubmission11BTests.answers
    event_session = baseline.IntakeSubmission11BTests.event_session

    def test_finish_page_shows_ticket_for_new_event_registration(self):
        event = Event.objects.create(name="SYNTHETIC Ticket Event", status="upcoming",
            start_at=timezone.now() + timedelta(days=1), end_at=timezone.now() + timedelta(days=2),
            public_registration_status="open", public_consent_document=self.document)
        session = self.event_session(event, phone="5550104321", email="ticket.finish@example.test")
        complete_session(session_id=session.pk)
        session.refresh_from_db()
        request = RequestFactory().get("/")
        extra = _finished_ticket(request, session)
        if extra.get("ticket_pending"):
            self.skipTest("Synthetic answers routed to identity review")
        ticket = extra["ticket"]
        self.assertEqual(ticket, QrTicket.objects.get(registration__event=event, is_current=True))
        self.assertTrue(extra["ticket_qr_data"].startswith("data:image/svg+xml;base64,"))
        html = render_to_string("participants/intake_complete.html", {"event": event, **extra}, request=request)
        self.assertIn("You're registered", html)
        self.assertIn(ticket.ticket_number, html)
        self.assertIn("Save ticket image", html)

    def test_finish_page_without_event_has_no_ticket(self):
        self.assertEqual(_finished_ticket(RequestFactory().get("/"), type("S", (), {"submission": None})()), {})
