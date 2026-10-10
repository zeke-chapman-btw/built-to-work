"""Build 7C-2 ticket allocation, rendering, and staff recovery checks."""
from datetime import timedelta
from io import BytesIO
from unittest import skipIf

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.core.models import AuditLog
from apps.participants.models import Participant
from .models import Event, EventRegistration, QrTicket, TicketAllocationBlock
from .services import register_participant, resolve_ticket
from .ticket_renderer import ticket_png_bytes
from .ticketing import allocate_ticket_number


class TicketWorkflowTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(name='Review Event', status=Event.Status.UPCOMING,
            start_at=now - timedelta(minutes=5), end_at=now + timedelta(hours=3))
        self.person = Participant.objects.create(first_name='Taylor', last_name='Testperson',
            contact_phone='5555550101', contact_email='taylor@example.test')
        self.registration, _ = register_participant(event=self.event, participant=self.person, actor=None,
            source=EventRegistration.Source.STAFF)
        self.ticket = self.registration.tickets.get(is_current=True)
        self.staff = get_user_model().objects.create_user(username='ticket-staff', password='test', is_staff=True)

    def test_complete_png_contains_separate_content_and_number_resolves(self):
        image = Image.open(BytesIO(ticket_png_bytes(self.ticket))).convert('RGB')
        self.assertEqual(image.size, (1100, 700))
        self.assertRegex(self.ticket.ticket_number, r'^\d{10}$')
        self.assertEqual(resolve_ticket('tel:' + self.ticket.ticket_number, expected_event=self.event).ticket, self.ticket)
        self.assertLess(image.crop((55, 245, 460, 650)).convert('L').getextrema()[0], 80)
        self.assertLess(image.crop((500, 255, 1050, 630)).convert('L').getextrema()[0], 80)
        self.assertEqual(self.client.get(reverse('events:ticket_image', args=[self.ticket.token])).status_code, 200)

    def test_staff_search_and_reprint_are_audited_without_reissue(self):
        url = reverse('events:ticket_recovery')
        self.assertEqual(self.client.get(url, {'q': self.ticket.ticket_number}).status_code, 302)
        self.assertEqual(self.client.post(reverse('events:ticket_reprint', args=[self.ticket.pk])).status_code, 302)
        self.client.force_login(self.staff, backend='django.contrib.auth.backends.ModelBackend')
        for query in (self.ticket.ticket_number, 'Taylor', self.person.contact_phone, self.person.contact_email):
            response = self.client.get(url, {'q': query, 'event': str(self.event.pk)})
            self.assertContains(response, self.ticket.ticket_number)
        self.assertEqual(self.client.get(url, {'event': 'not-a-uuid'}).status_code, 200)
        response = self.client.post(reverse('events:ticket_reprint', args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.ticket.token), response['Location'])
        self.assertEqual(QrTicket.objects.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action='ticket.recovery.viewed', object_id=str(self.ticket.pk)).exists())
        self.assertTrue(AuditLog.objects.filter(action='ticket.recovery.reprinted', object_id=str(self.ticket.pk)).exists())

    def test_public_ticket_number_does_not_disclose_person(self):
        response = self.client.get(reverse('events:ticket_lookup', args=[self.ticket.ticket_number]))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.person.first_name, response.content.decode())

    def test_overlap_and_range_exhaustion(self):
        block = TicketAllocationBlock.objects.get(name='cloud')
        with self.assertRaises(ValidationError):
            TicketAllocationBlock.objects.create(name='overlap', start_number=2, end_number=9, next_number=2)
        block.next_number = block.end_number
        block.save(update_fields=['next_number'])
        self.assertEqual(allocate_ticket_number(), f'{block.end_number:010d}')
        with self.assertRaises(ValidationError):
            allocate_ticket_number()


class TicketAllocationConcurrencyTests(TransactionTestCase):
    @skipIf(connection.vendor == 'sqlite', 'SQLite cannot verify row-lock allocation; run against PostgreSQL.')
    def test_parallel_allocations_are_unique(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            numbers = list(pool.map(lambda _: allocate_ticket_number(), range(12)))
        self.assertEqual(len(numbers), len(set(numbers)))
