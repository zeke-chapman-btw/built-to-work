from datetime import date, datetime, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from .event_reminder_calendar import (advance_day, business_days_after,
    latest_window, local_day, scheduled_morning, summary_scope)


class EventReminderCalendar18BTests(SimpleTestCase):
    def test_owner_closeout_examples_and_business_day_count(self):
        for end, expected in ((date(2026, 11, 15), date(2026, 11, 18)),
                              (date(2026, 11, 13), date(2026, 11, 18)),
                              (date(2026, 11, 16), date(2026, 11, 19))):
            with self.subTest(end=end):
                self.assertEqual(business_days_after(end), expected)

    def test_no_holiday_calendar_is_introduced(self):
        self.assertEqual(business_days_after(date(2026, 12, 24)), date(2026, 12, 29))

    def test_weekend_advance_moves_back_to_friday(self):
        self.assertEqual(advance_day(date(2026, 11, 17), 2), date(2026, 11, 13))
        self.assertEqual(advance_day(date(2026, 11, 16), 1), date(2026, 11, 13))

    def test_only_explicit_weekend_eligibility_keeps_a_weekend_slot(self):
        self.assertEqual(advance_day(date(2026, 11, 16), 1,
                         weekend_eligible=True), date(2026, 11, 15))

    def test_colliding_prep_windows_collapse_to_one_friday(self):
        self.assertEqual(latest_window(date(2026, 11, 16), (3, 1),
                         date(2026, 11, 13)), date(2026, 11, 13))

    def test_catchup_selects_latest_window_not_every_missed_notice(self):
        self.assertEqual(latest_window(date(2026, 11, 20), (7, 2),
                         date(2026, 11, 19)), date(2026, 11, 18))
        self.assertIsNone(latest_window(date(2026, 11, 20), (7, 2),
                         date(2026, 11, 20)))

    def test_unrelated_weekend_catchup_is_suppressed(self):
        self.assertIsNone(latest_window(date(2026, 11, 16), (3, 1),
                          date(2026, 11, 15)))
        self.assertEqual(latest_window(date(2026, 11, 16), (3, 1),
                         date(2026, 11, 15), work_on=lambda day: day == date(2026, 11, 15)),
                         date(2026, 11, 15))

    def test_event_zone_day_and_named_eastern_dst_are_reused(self):
        event = SimpleNamespace(timezone_name='America/Los_Angeles')
        self.assertEqual(local_day(datetime(2026, 11, 16, 3, tzinfo=timezone.utc), event),
                         date(2026, 11, 15))
        self.assertEqual(scheduled_morning(date(2026, 3, 6)).astimezone(timezone.utc).hour, 12)
        self.assertEqual(scheduled_morning(date(2026, 3, 9)).astimezone(timezone.utc).hour, 11)
        self.assertEqual(scheduled_morning(date(2026, 11, 16), event)
                         .astimezone(timezone.utc).hour, 15)

    def test_scope_sharing_and_weekend_event_identity(self):
        eastern = SimpleNamespace(timezone_name='America/New_York')
        pacific = SimpleNamespace(timezone_name='America/Los_Angeles')
        self.assertEqual(summary_scope(eastern, date(2026, 11, 16)), (None, eastern.timezone_name))
        self.assertEqual(summary_scope(eastern, date(2026, 11, 15)), (eastern, eastern.timezone_name))
        self.assertEqual(summary_scope(pacific, date(2026, 11, 16)), (pacific, pacific.timezone_name))

    def test_invalid_timing_is_rejected(self):
        with self.assertRaises(ValueError):
            business_days_after(date(2026, 11, 16), 0)
        with self.assertRaises(ValueError):
            advance_day(date(2026, 11, 16), 0)
