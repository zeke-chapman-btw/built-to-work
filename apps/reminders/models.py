"""Reminders that staff can set up in Settings → Reminders, without code.

A ReminderRule says what to watch (a *source*, defined in sources.py), when to
fire, who gets it and how (Bell, Email, Text). Built-in rows describe reminders
that already run elsewhere (task reminders, ticket confirmations, pay
statements) so the whole list lives in one place.
"""
import datetime

from django.conf import settings
from django.db import models


class ReminderSetup(models.Model):
    """One row: the master on/off switch."""
    is_on = models.BooleanField(default=False)
    turned_on_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    @classmethod
    def current(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class ReminderRole(models.Model):
    """A job ("Recruiting lead") that reminders can go to. Holders can change any time."""
    key = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    holders = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="reminder_roles")
    waiting_for = models.CharField(max_length=120, blank=True,
                                   help_text="Someone who isn't in the app yet. They're picked up automatically once they are.")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "name")
        default_permissions = ()

    def __str__(self):
        return self.name


class ReminderRule(models.Model):
    class Mode(models.TextChoices):
        BEFORE = "before", "Days before a date"
        AFTER = "after", "Days after a date"
        NEW = "new", "As soon as something new comes in"
        CHANGED = "changed", "When something changes"

    class Backup(models.TextChoices):
        ADMINS = "admins", "Administrators"
        NONE = "none", "No one"

    key = models.SlugField(max_length=60, unique=True)
    area = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=240, blank=True)
    source = models.CharField(max_length=40, blank=True)
    built_in = models.CharField(max_length=20, blank=True, help_text="Runs elsewhere in the app; shown here for the full picture.")
    enabled = models.BooleanField(default=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.NEW)
    days = models.CharField(max_length=40, blank=True, help_text="One or more numbers, like 7, 2.")
    business_days = models.BooleanField(default=False)
    repeat_every = models.PositiveSmallIntegerField(default=0, help_text="Days between repeats while it's still open. 0 = once.")
    send_time = models.TimeField(default=datetime.time(8, 0))
    weekdays_only = models.BooleanField(default=False)
    bell = models.BooleanField(default=True)
    email = models.BooleanField(default=False)
    text = models.BooleanField(default=False)
    recipients = models.JSONField(default=dict, blank=True)
    conditions = models.JSONField(default=dict, blank=True, help_text="Only items that match these go out.")
    backup = models.CharField(max_length=10, choices=Backup.choices, default=Backup.ADMINS)
    subject = models.CharField(max_length=160, blank=True)
    message = models.TextField(blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position", "name")
        default_permissions = ()

    def __str__(self):
        return self.name

    def day_list(self):
        values = []
        for part in (self.days or "").replace(" ", "").split(","):
            if part.isdigit():
                values.append(int(part))
        return sorted(set(values), reverse=True)

    def channels(self):
        return [c for c in ("bell", "email", "text") if getattr(self, c)]


class ReminderPreference(models.Model):
    """How one person wants a reminder delivered to them (overrides the reminder's own choice)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reminder_preferences")
    rule = models.ForeignKey(ReminderRule, on_delete=models.CASCADE, related_name="preferences")
    bell = models.BooleanField(default=True)
    email = models.BooleanField(default=False)
    text = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=("user", "rule"), name="reminders_one_preference")]

    def channels(self):
        return [c for c in ("bell", "email", "text") if getattr(self, c)]


class ReminderSend(models.Model):
    """One reminder to one person on one channel. The unique key stops repeats."""
    rule = models.ForeignKey(ReminderRule, on_delete=models.CASCADE, related_name="sends")
    ref = models.CharField(max_length=120)
    occurrence = models.CharField(max_length=60)
    target = models.CharField(max_length=80)
    channel = models.CharField(max_length=8)
    status = models.CharField(max_length=10, default="sent")
    error = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=("rule", "ref", "occurrence", "target", "channel"), name="reminders_send_once")]
        indexes = [models.Index(fields=("created_at",))]


class ReminderWatch(models.Model):
    """What a watched record looked like last time (for "when something changes")."""
    rule = models.ForeignKey(ReminderRule, on_delete=models.CASCADE, related_name="watches")
    ref = models.CharField(max_length=120)
    fingerprint = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=("rule", "ref"), name="reminders_watch_once")]


class ReminderNotice(models.Model):
    """An in-app reminder (the "Bell" channel), shown in Inbox → Reminders."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reminder_notices")
    rule = models.ForeignKey(ReminderRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="notices")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        default_permissions = ()
        indexes = [models.Index(fields=("user", "read_at"))]
