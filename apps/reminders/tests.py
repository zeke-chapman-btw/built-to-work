from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import engine, sources
from .models import ReminderNotice, ReminderRole, ReminderRule, ReminderSend, ReminderSetup

ET = ZoneInfo("America/New_York")
BACKEND = "django.contrib.auth.backends.ModelBackend"


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


class Base(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.admin = users.create_superuser(username="rm-admin", password="x-test-only", first_name="Ada", last_name="Admin",
                                            email="ada@example.test")
        self.staff = users.create_user(username="rm-staff", password="x-test-only", is_staff=True, first_name="Sam",
                                       last_name="Staff", email="sam@example.test")
        self.client.force_login(self.admin, backend=BACKEND)

    def turn_on(self, when=None):
        setup = ReminderSetup.current()
        setup.is_on, setup.turned_on_at = True, when or at(2030, 1, 1, 0)
        setup.save()


def fake_source(items, modes=("new",), related=None):
    return sources.Source("fake", "Other", "Fake", lambda today, now: list(items), modes, "the date",
                          related or {"owner": "Owner"}, {"name": "name"})


class SeedTests(Base):
    def test_spreadsheet_rows_and_roles_are_seeded(self):
        self.assertEqual(ReminderRule.objects.count(), 30)
        self.assertEqual(ReminderRule.objects.exclude(built_in="").count(), 11)
        self.assertEqual(set(ReminderRole.objects.values_list("waiting_for", flat=True)), {"Zeke Chapman", "Jason Collins", ""})
        for rule in ReminderRule.objects.filter(built_in=""):
            self.assertIn(rule.source, sources.SOURCES, rule.key)
            self.assertIn(rule.mode, sources.SOURCES[rule.source].modes, rule.key)
        self.assertEqual(ReminderRule.objects.get(key="staff_working_soon").channels(), ["text"])
        self.assertEqual(ReminderRule.objects.get(key="new_position").channels(), ["bell", "email"])

    def test_every_source_runs_on_an_empty_database(self):
        now = timezone.now()
        for source in sources.SOURCES.values():
            list(source.finder(now.date(), now))


class TimingTests(Base):
    def rule(self, **kw):
        return ReminderRule(key="t", name="T", source="fake", **kw)

    def test_before_fires_on_each_offset_after_send_time(self):
        rule = self.rule(mode="before", days="7, 2", send_time=time(8))
        item = sources.Item(ref="x", line="x", anchor=date(2030, 3, 10))
        self.assertIsNone(engine.occurrence(rule, item, at(2030, 3, 2, 9)))
        self.assertIsNone(engine.occurrence(rule, item, at(2030, 3, 3, 7)))
        self.assertEqual(engine.occurrence(rule, item, at(2030, 3, 3, 8)), "b7")
        self.assertEqual(engine.occurrence(rule, item, at(2030, 3, 5, 8)), "b7")
        self.assertEqual(engine.occurrence(rule, item, at(2030, 3, 8, 9)), "b2")
        self.assertIsNone(engine.occurrence(rule, item, at(2030, 3, 11, 9)))

    def test_after_with_business_days_and_repeat(self):
        rule = self.rule(mode="after", days="1", business_days=True, repeat_every=0, send_time=time(9))
        item = sources.Item(ref="x", line="x", anchor=date(2030, 3, 8))  # a Friday
        self.assertIsNone(engine.occurrence(rule, item, at(2030, 3, 9, 10)))
        self.assertEqual(engine.occurrence(rule, item, at(2030, 3, 11, 10)), "a0")
        weekly = self.rule(mode="after", days="3", repeat_every=7, send_time=time(8))
        self.assertEqual(engine.occurrence(weekly, item, at(2030, 3, 11, 9)), "a0")
        self.assertEqual(engine.occurrence(weekly, item, at(2030, 3, 18, 9)), "a1")

    def test_new_skips_old_items_unless_repeating(self):
        rule = self.rule(mode="new", repeat_every=0, send_time=time(8))
        old = sources.Item(ref="x", line="x", created=at(2030, 3, 1, 9))
        self.assertIsNone(engine.occurrence(rule, old, at(2030, 3, 1, 12), turned_on_at=at(2030, 3, 1, 10)))
        fresh = sources.Item(ref="y", line="y", created=at(2030, 3, 1, 11))
        self.assertEqual(engine.occurrence(rule, fresh, at(2030, 3, 1, 11, 5), turned_on_at=at(2030, 3, 1, 10)), "new")
        daily = self.rule(mode="new", repeat_every=1, send_time=time(8))
        self.assertEqual(engine.occurrence(daily, old, at(2030, 3, 3, 9), turned_on_at=at(2030, 3, 1, 10)), "n2")


class RecipientTests(Base):
    def test_role_waits_for_named_person_then_picks_them_up(self):
        role = ReminderRole.objects.get(key="recruiting_lead")
        rule = ReminderRule(key="r", name="R", recipients={"roles": ["recruiting_lead"]})
        self.assertEqual([t.user for t in engine.targets_for(rule, None)], [self.admin])
        jason = get_user_model().objects.create_user(username="jc", is_staff=True, first_name="Jason", last_name="Collins")
        self.assertEqual([t.user for t in engine.targets_for(rule, None)], [jason])
        role.holders.add(self.staff)
        self.assertEqual([t.user for t in engine.targets_for(rule, None)], [self.staff])

    def test_backup_only_when_nobody_is_found(self):
        item = sources.Item(ref="x", line="x", related={"owner": []})
        rule = ReminderRule(key="r", name="R", recipients={"related": ["owner"]}, backup="admins")
        self.assertEqual([t.user for t in engine.targets_for(rule, item)], [self.admin])
        rule.backup = "none"
        self.assertEqual(engine.targets_for(rule, item), [])


class SendingTests(Base):
    def make_rule(self, **kw):
        values = dict(key="fake-rule", name="Fake reminder", source="fake", mode="new", bell=True,
                      recipients={"related": ["owner"]}, subject="Check on {name}", message="{line}")
        values.update(kw)
        return ReminderRule.objects.create(**values)

    def test_paused_sends_nothing_and_on_sends_once(self):
        now = timezone.now()
        item = sources.Item(ref="a", line="Call SYNTHETIC Pat", created=now, fields={"name": "Pat"},
                            related={"owner": [sources.user_target(self.staff)]})
        self.make_rule(email=True)
        with patch.dict(sources.SOURCES, {"fake": fake_source([item])}):
            self.assertEqual(engine.run_pass(now)["sent"], 0)
            self.turn_on(now - timedelta(hours=1))
            engine.run_pass(now)
            engine.run_pass(now + timedelta(minutes=5))
        notice = ReminderNotice.objects.get()
        self.assertEqual((notice.user, notice.title), (self.staff, "Check on Pat"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sam@example.test"])

    def test_several_items_become_one_list(self):
        now = timezone.now()
        owner = [sources.user_target(self.staff)]
        items = [sources.Item(ref=str(i), line=f"Item {i}", created=now, related={"owner": owner}) for i in range(3)]
        self.make_rule()
        self.turn_on(now - timedelta(hours=1))
        with patch.dict(sources.SOURCES, {"fake": fake_source(items)}):
            engine.run_pass(now)
        notice = ReminderNotice.objects.get()
        self.assertEqual(notice.title, "Fake reminder (3)")
        self.assertIn("• Item 2", notice.body)

    def test_texts_wait_for_daytime_and_skip_stop(self):
        from apps.messaging import store, texting
        store.save(key="eztexting", values={"username": "btw"}, actor=self.admin, new_secret="pw-test-only")
        noon = at(2030, 3, 1, 12)
        target = sources.Target("s:1", "Pat Person", phone="(706) 555-0199")
        item = sources.Item(ref="a", line="x", created=noon, fields={"event": "Expo"}, related={"owner": [target]})
        self.make_rule(bell=False, text=True, message="Hi {first_name}, see you at {event}.")
        self.turn_on(noon - timedelta(hours=1))
        with patch.dict(sources.SOURCES, {"fake": fake_source([item])}), \
                patch("apps.events.sms_providers.send_sms", return_value="ez:1") as sent:
            engine.run_pass(at(2030, 3, 1, 22))
            sent.assert_not_called()
            engine.run_pass(noon)
            sent.assert_called_once_with("7065550199", "Hi Pat, see you at Expo.", idempotency_key=sent.call_args.kwargs["idempotency_key"])
            texting.receive({"fromNumber": "17065550199", "message": "STOP"})
            other = sources.Item(ref="b", line="x", created=noon, related={"owner": [target]})
        with patch.dict(sources.SOURCES, {"fake": fake_source([other])}), \
                patch("apps.events.sms_providers.send_sms") as sent2:
            engine.run_pass(noon + timedelta(minutes=10))
            sent2.assert_not_called()
        self.assertEqual(ReminderSend.objects.get(ref="b").status, "failed")

    def test_changed_mode_waits_for_a_real_change(self):
        noon = at(2030, 3, 1, 12)
        owner = [sources.user_target(self.staff)]
        self.make_rule(mode="changed")
        self.turn_on(noon)
        first = sources.Item(ref="e", line="Expo", fingerprint="aaa", related={"owner": owner})
        moved = sources.Item(ref="e", line="Expo moved", fingerprint="bbb", related={"owner": owner})
        with patch.dict(sources.SOURCES, {"fake": fake_source([first], modes=("changed",))}):
            engine.run_pass(noon)
            engine.run_pass(noon)
        self.assertFalse(ReminderNotice.objects.exists())
        with patch.dict(sources.SOURCES, {"fake": fake_source([moved], modes=("changed",))}):
            engine.run_pass(noon)
            engine.run_pass(noon)
        self.assertEqual(ReminderNotice.objects.count(), 1)


class EventSourceTests(Base):
    def test_staff_working_soon_finds_confirmed_staff(self):
        from apps.events.models import Event
        from apps.staffing.calendar import day_window
        from apps.staffing.models import AssignmentDay, StaffAssignment, StaffProfile
        today = timezone.localdate()
        start = datetime.combine(today + timedelta(days=2), time(13), ET)
        event = Event.objects.create(name="SYNTHETIC Expo", start_at=start, end_at=start + timedelta(hours=6), status="upcoming",
                                     timezone_name="America/New_York", location_name="Fairgrounds", city="Athens")
        profile = StaffProfile.objects.create(first_name="Sam", last_name="Staff", phone="(706) 555-0111", user=self.staff)
        row = StaffAssignment.objects.create(event=event, staff=profile, status="confirmed")
        AssignmentDay.objects.create(assignment=row, **day_window(event, start.date()))
        items = list(sources.staff_working_soon(today, timezone.now()))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].anchor, start.date())
        self.assertEqual(items[0].related["staff"][0].phone, "(706) 555-0111")
        self.assertEqual(items[0].fields["place"], "Fairgrounds, Athens")
        changed = list(sources.event_changed(today, timezone.now()))
        self.assertEqual(len(changed), 1)


class PageTests(Base):
    def test_list_page_and_editor(self):
        page = self.client.get(reverse("reminders:list"))
        self.assertContains(page, "Reminders are paused")
        self.assertContains(page, "Jason Collins")
        self.assertContains(page, "Runs elsewhere")
        rule = ReminderRule.objects.get(key="missing_info")
        panel = self.client.get(reverse("reminders:edit", args=[rule.pk]), HTTP_X_BTW_EDITOR="1")
        self.assertContains(panel, "Staff member who asked")
        self.assertNotContains(panel, "<script")
        response = self.client.post(reverse("reminders:edit", args=[rule.pk]), {
            "source": "missing_info", "enabled": "on", "name": rule.name, "area": "Participants", "mode": "after",
            "days": "5", "repeat_every": "7", "send_time": "09:30", "bell": "on", "email": "on", "related": ["owner"],
            "backup": "admins", "subject": rule.subject, "message": rule.message})
        self.assertEqual(response.status_code, 302)
        rule.refresh_from_db()
        self.assertEqual((rule.days, rule.send_time, rule.email), ("5", time(9, 30), True))
        built_in = ReminderRule.objects.get(key="task_assigned")
        self.assertContains(self.client.get(reverse("reminders:edit", args=[built_in.pk])), "Open where it's set")

    def test_new_reminder_and_role(self):
        response = self.client.post(reverse("reminders:new") + "?source=payroll_ready", {
            "source": "payroll_ready", "enabled": "on", "name": "Payroll nudge for Sam", "area": "Payroll", "mode": "new",
            "days": "", "repeat_every": "0", "send_time": "08:00", "bell": "on", "people": [str(self.staff.pk)],
            "backup": "admins", "subject": "Payroll ready", "message": "{line}"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ReminderRule.objects.get(key="payroll-nudge-for-sam").recipients, {"people": [str(self.staff.pk)]})
        bad = self.client.post(reverse("reminders:new") + "?source=payroll_ready", {
            "source": "payroll_ready", "enabled": "on", "name": "Nobody", "area": "Payroll", "mode": "new",
            "repeat_every": "0", "send_time": "08:00", "bell": "on", "backup": "admins"}, HTTP_X_BTW_EDITOR="1")
        self.assertContains(bad, "Pick who gets it", status_code=400)
        role = ReminderRole.objects.get(key="payroll_approver")
        self.client.post(reverse("reminders:role-edit", args=[role.pk]), {"name": role.name, "holders": [str(self.staff.pk)],
                                                                         "waiting_for": ""})
        self.assertEqual(list(role.holders.all()), [self.staff])

    def test_switch_and_managers_only(self):
        self.client.post(reverse("reminders:switch"), {"on": "1"})
        self.assertTrue(ReminderSetup.current().is_on)
        self.client.force_login(self.staff, backend=BACKEND)
        self.assertEqual(self.client.get(reverse("reminders:list")).status_code, 403)

    def test_inbox_reminders_tab(self):
        notice = ReminderNotice.objects.create(user=self.admin, title="Payroll ready to approve", body="Week of Mar 2")
        page = self.client.get(reverse("messaging:texts") + "?tab=reminders")
        self.assertContains(page, "Payroll ready to approve")
        self.client.post(reverse("reminders:read", args=[notice.pk]))
        notice.refresh_from_db()
        self.assertIsNotNone(notice.read_at)
        self.client.force_login(self.staff, backend=BACKEND)
        ReminderNotice.objects.create(user=self.staff, title="Hello Sam")
        self.assertContains(self.client.get(reverse("messaging:texts") + "?tab=reminders"), "Hello Sam")

    def test_settings_has_reminders_tab(self):
        self.assertContains(self.client.get(reverse("internal:settings")), reverse("reminders:list"))
