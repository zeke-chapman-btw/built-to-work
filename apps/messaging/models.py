import uuid

from django.conf import settings
from django.db import models


class GmailConnection(models.Model):
    """One staff member's permission for the app to send email as them.

    Only the long-lived refresh token is kept, encrypted with a key derived from the
    deployment secret. Disconnecting revokes it at Google and clears it here.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gmail_connection")
    google_email = models.EmailField()
    refresh_token_encrypted = models.TextField(blank=True)
    scopes = models.CharField(max_length=500, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    needs_reconnect = models.BooleanField(default=False)
    last_error = models.CharField(max_length=240, blank=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.user} · {self.google_email}"

    @property
    def active(self):
        return bool(self.refresh_token_encrypted) and not self.needs_reconnect


class SentEmail(models.Model):
    """One staff-written email, claimed before sending so a double click cannot send twice."""

    class Status(models.TextChoices):
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        UNCERTAIN = "uncertain", "Unconfirmed"

    operation_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_emails")
    from_address = models.EmailField()
    participant = models.ForeignKey("participants.Participant", null=True, blank=True, on_delete=models.PROTECT, related_name="app_emails")
    to_address = models.EmailField()
    subject = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SENDING)
    provider_reference = models.CharField(max_length=160, blank=True)
    error = models.CharField(max_length=240, blank=True)
    interaction = models.ForeignKey("crm.Interaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ("-created_at",)


class TextDeliveryResult(models.Model):
    """What EZ Texting reports after it accepted a text: delivered, failed, or still waiting."""

    class Result(models.TextChoices):
        PENDING = "pending", "Waiting for carrier"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Not delivered"

    provider_reference = models.CharField(max_length=160, unique=True)
    result = models.CharField(max_length=12, choices=Result.choices, default=Result.PENDING)
    checks = models.PositiveSmallIntegerField(default=0)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    detail = models.CharField(max_length=160, blank=True)

    class Meta:
        default_permissions = ()


class ConnectionSetting(models.Model):
    """Administrator-managed sign-in for a shared service (texting, automatic email).

    Secrets are stored encrypted and are never shown again once saved. When no
    setting is saved here, the app falls back to the Render environment values.
    """

    class Key(models.TextChoices):
        EZTEXTING = "eztexting", "Texting (EZ Texting)"
        EMAIL = "email", "Automatic email"
        SENDING = "sending", "Automatic messages"

    key = models.CharField(max_length=20, choices=Key.choices, unique=True)
    values = models.JSONField(default=dict, blank=True)
    secret_encrypted = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)
    last_test_ok = models.BooleanField(null=True, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_detail = models.CharField(max_length=240, blank=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.get_key_display()


MARKETING_WORDING = ("Send me job openings, training and event news from Built to Work by email. "
                     "I can unsubscribe at any time.")


class MarketingConsent(models.Model):
    """Whether a participant agreed to marketing email, with proof of what they agreed to.

    Separate from the required sign-up consent and from ticket messages. Mailchimp
    (when connected) only ever receives people whose status is ``on``.
    """

    class Status(models.TextChoices):
        ON = "on", "On"
        OFF = "off", "Off"
        UNSUBSCRIBED = "unsubscribed", "Unsubscribed"
        BOUNCED = "bounced", "Email bounced"

    class Source(models.TextChoices):
        SIGNUP = "signup", "Sign-up form"
        STAFF = "staff", "Staff"
        MAILCHIMP = "mailchimp", "Mailchimp"

    participant = models.OneToOneField("participants.Participant", on_delete=models.CASCADE, related_name="marketing_consent")
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OFF)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.STAFF)
    wording = models.TextField(blank=True)
    note = models.CharField(max_length=300, blank=True)
    changed_at = models.DateTimeField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submission = models.ForeignKey("participants.RegistrationSubmission", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # Mailchimp bookkeeping
    needs_sync = models.BooleanField(default=True, db_index=True)
    synced_email = models.EmailField(blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    sync_error = models.CharField(max_length=240, blank=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.participant} · {self.get_status_display()}"
