"""Simple event responsibility and checklist snapshots; no readiness certification."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from apps.core.models import UUIDModel, TimestampedModel


class ChecklistPhase(models.TextChoices):
    PREPARE="prepare","Pre-event preparation"
    ARRIVAL="arrival","Arrival and setup"
    LIVE="live","Live operations"
    CLOSEOUT="closeout","Closeout and pack-up"


class ChecklistMaster(UUIDModel, TimestampedModel):
    key=models.CharField(max_length=32,unique=True,default="btw")
    title=models.CharField(max_length=160,default="BTW event checklist")
    class Meta:
        permissions=[("manage_master_checklist","Can edit the standard BTW master checklist")]
    def __str__(self):return self.title


class MasterChecklistCategory(UUIDModel, TimestampedModel):
    master=models.ForeignKey(ChecklistMaster,on_delete=models.PROTECT,related_name="categories")
    title=models.CharField(max_length=160)
    phase=models.CharField(max_length=16,choices=ChecklistPhase.choices)
    position=models.PositiveSmallIntegerField(default=0)
    is_active=models.BooleanField(default=True)
    class Meta:ordering=("position","id")
    def __str__(self):return self.title


class MasterChecklistItem(UUIDModel, TimestampedModel):
    category=models.ForeignKey(MasterChecklistCategory,on_delete=models.PROTECT,related_name="items")
    title=models.CharField(max_length=200)
    details=models.TextField(blank=True)
    is_required=models.BooleanField(default=False)
    position=models.PositiveSmallIntegerField(default=0)
    is_active=models.BooleanField(default=True)
    class Meta:ordering=("position","id")
    def __str__(self):return self.title


class EventExecution(UUIDModel, TimestampedModel):
    event=models.OneToOneField("events.Event",on_delete=models.PROTECT,related_name="execution")
    primary_manager=models.ForeignKey("staffing.StaffProfile",null=True,blank=True,on_delete=models.PROTECT,related_name="managed_event_executions")
    master_revision=models.CharField(max_length=40,blank=True)
    initialized_at=models.DateTimeField(default=timezone.now)
    actual_ended_at=models.DateTimeField(null=True,blank=True)
    ended_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="events_ended")
    closed_at=models.DateTimeField(null=True,blank=True)
    closed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="events_closed_out")
    last_reopened_at=models.DateTimeField(null=True,blank=True)
    last_reopened_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="event_closeouts_reopened")
    closeout_task=models.OneToOneField("operations.Task",null=True,blank=True,on_delete=models.PROTECT,related_name="event_closeout_execution")
    class Meta:
        permissions=[("manage_event_execution","Can administer event managers and execution")]
        constraints=[models.CheckConstraint(condition=Q(closed_at__isnull=True)|Q(actual_ended_at__isnull=False),name="ops_closeout_requires_actual_end")]
    def __str__(self):return str(self.event)


class EventChecklistCategory(UUIDModel, TimestampedModel):
    execution=models.ForeignKey(EventExecution,on_delete=models.PROTECT,related_name="categories")
    source_category=models.ForeignKey(MasterChecklistCategory,null=True,blank=True,on_delete=models.PROTECT)
    title=models.CharField(max_length=160)
    phase=models.CharField(max_length=16,choices=ChecklistPhase.choices)
    position=models.PositiveSmallIntegerField(default=0)
    class Meta:ordering=("position","id")
    def __str__(self):return self.title


class EventChecklistItem(UUIDModel, TimestampedModel):
    class State(models.TextChoices):
        TODO="todo","To do"
        DONE="done","Completed"
        NA="na","Not applicable"
    category=models.ForeignKey(EventChecklistCategory,on_delete=models.PROTECT,related_name="items")
    source_item=models.ForeignKey(MasterChecklistItem,null=True,blank=True,on_delete=models.PROTECT)
    original_title=models.CharField(max_length=200,blank=True)
    title=models.CharField(max_length=200)
    details=models.TextField(blank=True)
    is_required=models.BooleanField(default=False)
    required_from_master=models.BooleanField(default=False,editable=False)
    position=models.PositiveSmallIntegerField(default=0)
    assigned_staff=models.ForeignKey("staffing.StaffProfile",null=True,blank=True,on_delete=models.PROTECT,related_name="checklist_responsibilities")
    state=models.CharField(max_length=8,choices=State.choices,default=State.TODO)
    completed_at=models.DateTimeField(null=True,blank=True)
    completed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="checklist_items_completed")
    na_reason=models.TextField(blank=True)
    archived_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=("position","id")
        constraints=[
            models.CheckConstraint(condition=Q(required_from_master=False)|Q(is_required=True),name="ops_master_required_retained"),
            models.CheckConstraint(condition=(Q(state="done",completed_at__isnull=False)|(~Q(state="done")&Q(completed_at__isnull=True,completed_by__isnull=True))),name="ops_checklist_completion_state"),
            models.CheckConstraint(condition=~Q(state="na")|~Q(na_reason=""),name="ops_checklist_na_reason"),
        ]
    def __str__(self):return self.title
