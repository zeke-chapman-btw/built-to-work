"""Operational staff records reuse the existing User and Event identities."""
from django.conf import settings
from django.db import models
from django.db.models import Q, F
from apps.core.models import UUIDModel, TimestampedModel


class StaffProfile(UUIDModel, TimestampedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    street_address = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    zip_code = models.CharField(max_length=24, blank=True)
    shirt_size = models.CharField(max_length=12, blank=True, choices=[(s, s) for s in ("XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL")])
    is_active = models.BooleanField(default=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff_profile")
    internal_notes = models.TextField(blank=True)

    class Meta:
        ordering = ("last_name", "first_name", "pk")
        permissions = [("link_staff_account", "Can explicitly link staff profiles to existing accounts")]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class StaffingPlan(UUIDModel, TimestampedModel):
    event = models.OneToOneField("events.Event", on_delete=models.PROTECT, related_name="staffing_plan")
    required_staff = models.PositiveSmallIntegerField(default=0)
    is_published = models.BooleanField(default=False)
    signup_deadline = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [("manage_event_staffing", "Can manage event needs, requests and assignments")]


class DailyStaffingNeed(UUIDModel):
    plan = models.ForeignKey(StaffingPlan, on_delete=models.PROTECT, related_name="daily_needs")
    date = models.DateField()
    required_staff = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("date",)
        constraints = [models.UniqueConstraint(fields=("plan", "date"), name="staffing_unique_daily_need")]


class AvailabilityRequest(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="staff_availability")
    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="availability_requests")
    requested_dates = models.JSONField(default=list)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="staffing_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("event", "staff"), condition=Q(status__in=("pending", "approved")), name="staffing_one_active_request")]


class StaffAssignment(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        REPLACED = "replaced", "Replaced by revision"
        REMOVED = "removed", "Removed"
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="staff_assignments")
    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="assignments")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.CONFIRMED)
    role = models.CharField(max_length=80, blank=True)
    source_request = models.ForeignKey(AvailabilityRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="assignments")
    supersedes = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replacement")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff_assignment_changes")
    change_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=("event", "staff"), condition=Q(status="confirmed"), name="staffing_one_confirmed_assignment")]


class AssignmentDay(UUIDModel):
    assignment = models.ForeignKey(StaffAssignment, on_delete=models.PROTECT, related_name="days")
    date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    arrival_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("date",)
        constraints = [
            models.UniqueConstraint(fields=("assignment", "date"), name="staffing_one_assignment_day"),
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="staffing_work_end_after_start"),
        ]
