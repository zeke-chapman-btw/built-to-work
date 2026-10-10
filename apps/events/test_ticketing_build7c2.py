from django.test import SimpleTestCase

from .ticket_renderer import qr_png_bytes
from .ticketing import qr_payload


class TicketPresentationTests(SimpleTestCase):
    def test_qr_payload_is_tel_number(self):
        self.assertEqual(qr_payload("0000000001"), "tel:0000000001")

    def test_qr_png_is_a_valid_png_signature(self):
        image = qr_png_bytes("tel:0000000001")
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image), 100)
