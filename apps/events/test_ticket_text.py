from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo
from django.test import SimpleTestCase
from .ticket_text import event_dates, event_place

NY = ZoneInfo("America/New_York")


def ev(start, end, **kw):
    return NS(start_at=start, end_at=end, timezone_name="America/New_York", **kw)


class TicketTextTests(SimpleTestCase):
    def test_dates(self):
        self.assertEqual(event_dates(ev(datetime(2026, 12, 3, 9, tzinfo=NY), datetime(2026, 12, 3, 15, tzinfo=NY))), "Thursday, December 3, 2026")
        self.assertEqual(event_dates(ev(datetime(2026, 12, 3, 9, tzinfo=NY), datetime(2026, 12, 4, 15, tzinfo=NY))), "Dec 3–4, 2026")
        self.assertEqual(event_dates(ev(datetime(2026, 11, 30, 9, tzinfo=NY), datetime(2026, 12, 1, 15, tzinfo=NY))), "Nov 30 – Dec 1, 2026")
        self.assertEqual(event_dates(ev(datetime(2026, 12, 31, 9, tzinfo=NY), datetime(2027, 1, 1, 15, tzinfo=NY))), "Dec 31, 2026 – Jan 1, 2027")

    def test_place(self):
        self.assertEqual(event_place(NS(location_name="Training Center", city="Augusta", state="GA", address_line_1="")), "Training Center · Augusta, GA")
        self.assertEqual(event_place(NS(location_name="", city="", state="", address_line_1="1 Main St")), "1 Main St")
        self.assertEqual(event_place(NS(location_name="", city="", state="", address_line_1="")), "")
