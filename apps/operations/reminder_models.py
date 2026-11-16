"""Reminder configuration and execution history, separate from human task edits."""
from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel, UUIDModel


class TaskReminderPolicy(TimestampedModel):
    key = models.CharField(primary_key=True, max_length=48)
    event = models.OneToOneField('events.Event', null=True, blank=True,
                                on_delete=models.PROTECT, related_name='reminder_policy')
    advance_enabled = models.BooleanField(null=True, blank=True, default=None)
    activity_enabled = models.BooleanField(null=True, blank=True, default=None)
    completion_enabled = models.BooleanField(null=True, blank=True, default=None)
    staffing_enabled = models.BooleanField(null=True, blank=True, default=None)
    preparation_enabled = models.BooleanField(null=True, blank=True, default=None)
    closeout_enabled = models.BooleanField(null=True, blank=True, default=None)
    staffing_reviews_enabled = models.BooleanField(null=True, blank=True, default=None)
    verification_reviews_enabled = models.BooleanField(null=True, blank=True, default=None)
    job_reviews_enabled = models.BooleanField(null=True, blank=True, default=None)
    customer_reviews_enabled = models.BooleanField(null=True, blank=True, default=None)
    staffing_days = models.JSONField(null=True, blank=True, default=None)
    preparation_days = models.JSONField(null=True, blank=True, default=None)
    closeout_days = models.PositiveSmallIntegerField(null=True, blank=True, default=None)
    staffing_review_recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='staffing_reminder_policies')
    verification_review_recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='verification_reminder_policies')
    job_review_recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='job_reminder_policies')
    customer_review_recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='customer_reminder_policies')

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(key='company', event__isnull=True) |
                       (~models.Q(key='company') & models.Q(event__isnull=False))),
            name='ops_reminder_policy_scope'),
            models.CheckConstraint(condition=(models.Q(event__isnull=True) |
                models.Q(staffing_review_recipient__isnull=True,
                         verification_review_recipient__isnull=True,
                         customer_review_recipient__isnull=True,
                         job_review_recipient__isnull=True)),
                name='ops_reminder_reviewers_company'),
            models.CheckConstraint(condition=(models.Q(closeout_days__isnull=True) |
                models.Q(closeout_days__gte=3, closeout_days__lte=14)),
                name='ops_reminder_closeout_delay')]


class TaskReminderPreference(UUIDModel, TimestampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='task_reminder_preference')
    activity_updates = models.BooleanField(null=True, blank=True, default=None)
    snoozes = models.JSONField(default=dict, blank=True)


class TaskReminderJob(UUIDModel, TimestampedModel):
    class Kind(models.TextChoices):
        ADVANCE = 'advance', 'Advance reminder'
        DUE = 'due', 'Deadline reminder'
        SUMMARY = 'summary', 'Morning summary'
        SOURCE = 'source', 'Source reminder'

    class State(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CLAIMED = 'claimed', 'Claimed'
        DELIVERED = 'delivered', 'Delivered'
        COVERED = 'covered', 'Included in summary'
        EMPTY = 'empty', 'No content'
        SUPERSEDED = 'superseded', 'Superseded'
        FAILED = 'failed', 'Retry needed'

    key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                  related_name='task_reminder_jobs')
    task = models.ForeignKey('operations.Task', null=True, blank=True,
                             on_delete=models.PROTECT, related_name='reminder_jobs')
    event = models.ForeignKey('events.Event', null=True, blank=True,
                              on_delete=models.PROTECT, related_name='task_reminder_jobs')
    local_date = models.DateField()
    timezone_name = models.CharField(max_length=64, default='America/New_York')
    scheduled_for = models.DateTimeField(db_index=True)
    available_at = models.DateTimeField(db_index=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    source_ref = models.JSONField(default=dict, blank=True)
    summary = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                                related_name='source_jobs')
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    through_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    attempt_history = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [models.Index(fields=('state', 'available_at'), name='ops_reminder_claim_idx'),
                   models.Index(fields=('recipient', 'kind', '-scheduled_for'), name='ops_reminder_recipient_idx')]
        constraints = [models.CheckConstraint(
            condition=(models.Q(kind='summary', task__isnull=True, source_ref={}, summary__isnull=True) |
                       models.Q(kind__in=('advance', 'due'), task__isnull=False,
                                source_ref={}, summary__isnull=True) |
                       (models.Q(kind='source', task__isnull=True, summary__isnull=False) &
                        ~models.Q(source_ref={}))),
            name='ops_reminder_job_task_kind')]


class TaskReminderItem(UUIDModel):
    """Unique coverage receipts prevent summary/deadline/activity duplication."""
    job = models.ForeignKey(TaskReminderJob, on_delete=models.PROTECT, related_name='items')
    task = models.ForeignKey('operations.Task', on_delete=models.PROTECT,
                             null=True, blank=True, related_name='reminder_items')
    source_ref = models.JSONField(default=dict, blank=True)
    source_event = models.ForeignKey('core.AuditLog', null=True, blank=True,
                                     on_delete=models.PROTECT, related_name='reminder_items')
    kind = models.CharField(max_length=12)
    dedupe_key = models.CharField(max_length=64, unique=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(task__isnull=False, source_ref={}) |
            (models.Q(task__isnull=True, source_event__isnull=True) & ~models.Q(source_ref={}))),
            name='ops_reminder_item_source')]


class TaskReminderSchedulerState(models.Model):
    key = models.CharField(primary_key=True, max_length=32, default='task-reminders')
    last_tick_at = models.DateTimeField(null=True, blank=True)
    last_automatic_at = models.DateTimeField(null=True, blank=True)
    automatic_worker_id = models.CharField(max_length=80, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_planned_at = models.DateTimeField(null=True, blank=True)
    scan_after = models.CharField(max_length=36, blank=True)
    worker_id = models.CharField(max_length=80, blank=True)
    last_jobs_processed = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    source_scan = models.JSONField(default=dict, blank=True)
    responsibility_health = models.JSONField(default=dict, blank=True)
