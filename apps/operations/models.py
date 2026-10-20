from django.conf import settings
from django.db import models
from apps.core.models import UUIDModel, TimestampedModel
class Task(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        TODO="todo", "To Do"
        PROGRESS="in_progress", "In Progress"
        COMPLETED="completed", "Completed"
        CANCELLED="cancelled", "Cancelled"
    class Priority(models.TextChoices):
        LOW="low", "Low"
        NORMAL="normal", "Normal"
        HIGH="high", "High"
    title=models.CharField(max_length=200)
    description=models.TextField(blank=True)
    assigned_staff=models.ForeignKey("staffing.StaffProfile",on_delete=models.PROTECT,related_name="tasks")
    event=models.ForeignKey("events.Event",null=True,blank=True,on_delete=models.PROTECT,related_name="operational_tasks")
    due_date=models.DateField(null=True,blank=True)
    priority=models.CharField(max_length=10,choices=Priority.choices,default=Priority.NORMAL)
    status=models.CharField(max_length=16,choices=Status.choices,default=Status.TODO)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="operational_tasks_created")
    completed_at=models.DateTimeField(null=True,blank=True)
    completed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="operational_tasks_completed")
    archived_at=models.DateTimeField(null=True,blank=True,db_index=True)
    archived_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="operational_tasks_archived")
    class Meta:
        ordering=("due_date","-created_at")
        permissions=[("manage_tasks","Can manage all operational tasks and assignments")]
        constraints=[models.CheckConstraint(condition=(models.Q(status="completed",completed_at__isnull=False)|(~models.Q(status="completed") & models.Q(completed_at__isnull=True,completed_by__isnull=True))),name="operations_task_completion_state")]
    def __str__(self): return self.title
class Visibility(models.TextChoices):
    MANAGERS="managers", "Managers only"
    STAFF="staff", "Assigned event staff"
class EventNote(UUIDModel, TimestampedModel):
    event=models.ForeignKey("events.Event",on_delete=models.PROTECT,related_name="operational_notes")
    content=models.TextField()
    visibility=models.CharField(max_length=12,choices=Visibility.choices,default=Visibility.MANAGERS)
    author=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name="operational_notes")
    edited_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL,related_name="operational_notes_edited")
    archived_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=("created_at","pk")
        permissions=[("manage_event_notes","Can manage event operational notes")]
class EventDocument(UUIDModel, TimestampedModel):
    event=models.ForeignKey("events.Event",on_delete=models.PROTECT,related_name="operational_documents")
    title=models.CharField(max_length=200)
    description=models.CharField(max_length=500,blank=True)
    visibility=models.CharField(max_length=12,choices=Visibility.choices,default=Visibility.MANAGERS)
    original_filename=models.CharField(max_length=255)
    content_type=models.CharField(max_length=80)
    file_size=models.PositiveBigIntegerField()
    sha256=models.CharField(max_length=64)
    storage_key=models.CharField(max_length=255,blank=True)
    is_demo=models.BooleanField(default=False)
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name="operational_documents_uploaded")
    archived_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=("-created_at","pk")
        permissions=[("manage_event_documents","Can manage event operational documents")]
