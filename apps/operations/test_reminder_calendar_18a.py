from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .reminder_calendar import (EASTERN, morning, next_applicable_morning,
                                next_working_day, previous_working_day,
                                scope_for, weekend_applies)


class ReminderCalendarTests(SimpleTestCase):
    def task(self, *, zone=EASTERN, work=None):
        event = SimpleNamespace(pk='sample-event', timezone_name=zone,
                                start_at=datetime(2026, 11, 16, 15, tzinfo=timezone.utc))
        return SimpleNamespace(event_id=event.pk, event=event, weekend_work_date=work,
                               due_date=date(2026, 11, 13))

    def test_monday_advance_is_friday_not_sunday(self):
        self.assertEqual(previous_working_day(date(2026, 11, 16)), date(2026, 11, 13))
        self.assertEqual(next_working_day(date(2026, 11, 13)), date(2026, 11, 16))

    def test_weekend_deadlines_have_a_friday_advance(self):
        for day in (date(2026, 11, 14), date(2026, 11, 15)):
            self.assertEqual(previous_working_day(day), date(2026, 11, 13))

    def test_eastern_spring_and_fall_change_utc_time(self):
        for day, hour in [(date(2026, 3, 6), 12), (date(2026, 3, 9), 11),
                          (date(2026, 10, 30), 11), (date(2026, 11, 2), 12)]:
            self.assertEqual(morning(day).astimezone(timezone.utc).hour, hour)

    def test_other_event_zone_uses_its_own_seven(self):
        self.assertEqual(morning(date(2026, 11, 13), 'America/Los_Angeles')
                         .astimezone(timezone.utc).hour, 15)

    def test_event_link_or_weekend_due_date_alone_is_not_weekend_work(self):
        task = self.task()
        task.due_date = date(2026, 11, 14)
        self.assertFalse(weekend_applies(task, date(2026, 11, 14)))

    def test_saturday_setup_before_monday_event_is_explicitly_eligible(self):
        task = self.task(work=date(2026, 11, 14))
        self.assertTrue(weekend_applies(task, date(2026, 11, 14)))
        self.assertTrue(weekend_applies(task, date(2026, 11, 15)))
        self.assertFalse(weekend_applies(task, date(2026, 11, 21)))
        self.assertFalse(weekend_applies(task, date(2026, 11, 13)))

    def test_weekend_work_uses_event_scope_even_in_eastern(self):
        task = self.task(work=date(2026, 11, 14))
        self.assertEqual(scope_for(task, date(2026, 11, 14)), (task.event, EASTERN))
        self.assertEqual(scope_for(task, date(2026, 11, 13)), (None, EASTERN))
        task.weekend_work_date = None
        self.assertEqual(scope_for(task), (None, EASTERN))

    def test_western_event_uses_event_scope_on_weekdays(self):
        task = self.task(zone='America/Los_Angeles')
        self.assertEqual(scope_for(task), (task.event, 'America/Los_Angeles'))

    def test_next_morning_skips_unrelated_weekend(self):
        task = self.task()
        result = next_applicable_morning(task, datetime(2026, 11, 13, 20, tzinfo=timezone.utc))
        self.assertEqual(result, morning(date(2026, 11, 16)))

    def test_next_morning_includes_actual_saturday_setup(self):
        task = self.task(zone='America/Los_Angeles', work=date(2026, 11, 14))
        result = next_applicable_morning(task, datetime(2026, 11, 13, 20, tzinfo=timezone.utc))
        self.assertEqual(result, morning(date(2026, 11, 14), 'America/Los_Angeles'))

    @override_settings(DEBUG=False, BTW_TASK_REMINDERS_ENABLED=True, BTW_PREVIEW_GATE_ENABLED=True,
                       EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_automatic_worker_never_runs_on_other_render_services(self):
        from .reminder_runtime import permitted
        with patch.dict('os.environ',{'RENDER_SERVICE_ID':'SAMPLE-production-service'}):
            self.assertFalse(permitted())
        with patch.dict('os.environ',{'RENDER_SERVICE_ID':'srv-db8n044s728c73aaet00'}):
            self.assertTrue(permitted())

    @override_settings(DEBUG=False, BTW_TASK_REMINDERS_ENABLED=True, BTW_PREVIEW_GATE_ENABLED=False)
    def test_local_worker_requires_debug_and_explicit_opt_in(self):
        from .reminder_runtime import permitted
        with patch.dict('os.environ',{'RENDER_SERVICE_ID':'','BTW_TASK_REMINDERS_LOCAL_WORKER':'true'}):
            self.assertFalse(permitted())
