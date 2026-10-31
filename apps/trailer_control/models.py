import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q


class ControlConfiguration(models.Model):
    trailer = models.OneToOneField("events.TrailerInstance", on_delete=models.PROTECT)
    preparation = models.ForeignKey("events.OfflineEventPreparation", null=True, blank=True, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)
    sampled_at = models.DateTimeField(null=True, blank=True)
    worker_seen_at = models.DateTimeField(null=True, blank=True)
    health = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("view_control_center", "View local Trailer Control Center"),
                       ("manage_control_center", "Manage local trailer context and devices"),
                       ("request_trailer_sync", "Request bounded trailer synchronization"),
                       ("publish_trailer_scores", "Approve anonymous event score publication")]


class Device(models.Model):
    class Kind(models.TextChoices):
        KIOSK="kiosk", "Kiosk"
        SIMULATOR="simulator", "Simulator"
        LEADERBOARD="leaderboard", "Leaderboard TV"
        VIDEO="video", "Video TV"
        OTHER="other", "Other"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trailer = models.ForeignKey("events.TrailerInstance", on_delete=models.PROTECT, related_name="control_devices")
    station = models.ForeignKey("stations.Station", null=True, blank=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    connection = models.CharField(max_length=12, choices=[("unknown","Unknown"),("wifi","Wi-Fi (declared)"),("ethernet","Ethernet (declared)")], default="unknown")
    active = models.BooleanField(default=True)
    credential_hash = models.CharField(max_length=64, blank=True)
    credential_revision = models.PositiveIntegerField(default=1)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    application_state = models.CharField(max_length=16, blank=True)
    playback_position = models.FloatField(null=True, blank=True)
    playback_advancing = models.BooleanField(default=False)
    current_boot = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class HeartbeatCursor(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    boot_id = models.UUIDField()
    sequence = models.PositiveBigIntegerField()
    retired = models.BooleanField(default=False)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("device","boot_id"), name="tcc_device_boot_unique")]


class DisplayBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.OneToOneField(Device, on_delete=models.PROTECT)
    preparation = models.ForeignKey("events.OfflineEventPreparation", on_delete=models.PROTECT)
    channel = models.CharField(max_length=16, choices=[("leaderboard","Leaderboard"),("video","Video")])
    profile_station = models.ForeignKey("stations.Station", null=True, blank=True, on_delete=models.PROTECT)
    pairing_hash = models.CharField(max_length=64, blank=True)
    pairing_expires_at = models.DateTimeField(null=True, blank=True)
    pairing_attempts = models.PositiveIntegerField(default=0)
    session_revision = models.PositiveIntegerField(default=1)
    media_name = models.CharField(max_length=200, blank=True)
    media_sha256 = models.CharField(max_length=64, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PublicationGrant(models.Model):
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT)
    participant = models.ForeignKey("participants.Participant", on_delete=models.PROTECT)
    alias = models.CharField(max_length=24)
    published = models.BooleanField(default=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=300)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("event","participant"),name="tcc_event_person_grant_unique"),
                     models.UniqueConstraint(fields=("event","alias"),name="tcc_event_alias_unique")]


class SyncRunRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trailer = models.ForeignKey("events.TrailerInstance", on_delete=models.PROTECT)
    preparation = models.ForeignKey("events.OfflineEventPreparation", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    request_key = models.UUIDField(unique=True)
    direction = models.CharField(max_length=12, choices=[("outbound","Send queued data"),("inbound","Download event updates")])
    status = models.CharField(max_length=12, choices=[("pending","Pending"),("running","Running"),("finished","Finished"),("failed","Needs attention")],default="pending")
    reason = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("trailer",),condition=Q(status__in=("pending","running")),name="tcc_one_active_sync_request")]
