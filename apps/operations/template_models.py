"""Reusable definitions and immutable revision/application evidence (Build 10B)."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from apps.core.models import UUIDModel, TimestampedModel

KINDS = (("assessment", "Quizzes & Tests"), ("station", "Stations"),
         ("game", "Game"), ("registration", "Registration settings"))

class OperationsTemplate(UUIDModel, TimestampedModel):
    name = models.CharField(max_length=160)
    retired_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="operations_templates_created")
    retired_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="operations_templates_retired")
    class Meta:
        ordering = ("name", "pk")
        permissions = [
            ("manage_operations_templates", "Can administer operations templates and publish revisions"),
            ("view_event_operations_setup", "Can view event operations configuration setup"),
            ("apply_operations_templates", "Can apply a published operations template to an event"),
        ]
    def __str__(self): return self.name

class OperationsTemplateRevision(UUIDModel, TimestampedModel):
    template = models.ForeignKey(OperationsTemplate, on_delete=models.PROTECT, related_name="revisions")
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, max_length=2000)
    status = models.CharField(max_length=12, choices=(("draft", "Draft"), ("published", "Published")), default="draft")
    not_applicable = models.JSONField(default=list, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    digest = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="operations_revisions_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="operations_revisions_updated")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="operations_revisions_published")
    published_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ("-number",)
        constraints = [
            models.UniqueConstraint(fields=("template", "number"), name="ops_template_revision_number"),
            models.UniqueConstraint(fields=("template",), condition=Q(status="draft"), name="ops_template_one_draft"),
            models.CheckConstraint(condition=Q(number__gte=1), name="ops_template_positive_revision"),
            models.CheckConstraint(condition=Q(status="draft", published_at__isnull=True) | Q(status="published", published_at__isnull=False), name="ops_template_publication_state"),
        ]
    def clean(self):
        super().clean()
        if not isinstance(self.not_applicable, list) or any(x not in dict(KINDS) for x in self.not_applicable) or len(set(self.not_applicable)) != len(self.not_applicable):
            raise ValidationError("Choose valid, distinct Not Applicable sections.")
        if self.pk and type(self).objects.filter(pk=self.pk, status="published").exists():
            raise ValidationError("Published revisions are immutable. Create a new revision.")
        if self.status == "published" and (not self.snapshot or not self.digest):
            raise ValidationError("Publish through the controlled publication workflow.")
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
    def __str__(self): return f"{self.name} · revision {self.number}"

class OperationsTemplateResource(UUIDModel):
    revision = models.ForeignKey(OperationsTemplateRevision, on_delete=models.PROTECT, related_name="resources")
    kind = models.CharField(max_length=16, choices=KINDS)
    question_set = models.ForeignKey("assessments.QuestionSet", null=True, blank=True, on_delete=models.PROTECT, related_name="operations_template_resources")
    station = models.ForeignKey("stations.Station", null=True, blank=True, on_delete=models.PROTECT, related_name="operations_template_resources")
    game = models.ForeignKey("games.GameDefinition", null=True, blank=True, on_delete=models.PROTECT, related_name="operations_template_resources")
    options = models.JSONField(default=dict, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    class Meta:
        ordering = ("position", "pk")
        constraints = [
            models.CheckConstraint(condition=(Q(kind="assessment", question_set__isnull=False, station__isnull=True, game__isnull=True) | Q(kind="station", question_set__isnull=True, station__isnull=False, game__isnull=True) | Q(kind="game", question_set__isnull=True, station__isnull=True, game__isnull=False) | Q(kind="registration", question_set__isnull=True, station__isnull=True, game__isnull=True)), name="ops_template_resource_reference"),
            models.UniqueConstraint(fields=("revision", "question_set"), condition=Q(kind="assessment"), name="ops_template_unique_set"),
            models.UniqueConstraint(fields=("revision", "station"), condition=Q(kind="station"), name="ops_template_unique_station"),
            models.UniqueConstraint(fields=("revision", "kind"), condition=Q(kind__in=("game", "registration")), name="ops_template_single_resource"),
        ]
    def clean(self):
        super().clean()
        if self.revision_id and self.revision.status != "draft":
            raise ValidationError("Only draft revision resources can be edited.")
        if not isinstance(self.options, dict): raise ValidationError("Resource settings must be an object.")
        if self.revision_id and self.kind in self.revision.not_applicable:
            raise ValidationError("A Not Applicable section cannot contain resources.")
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

class EventOperationsApplication(UUIDModel):
    event = models.OneToOneField("events.Event", on_delete=models.PROTECT, related_name="operations_template_application")
    revision = models.ForeignKey(OperationsTemplateRevision, on_delete=models.PROTECT, related_name="applications")
    snapshot = models.JSONField()
    confirmation_key = models.UUIDField(unique=True)
    event_before_digest = models.CharField(max_length=64)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="operations_templates_applied")
    applied_at = models.DateTimeField(auto_now_add=True)
    def save(self, *args, **kwargs):
        if not self._state.adding: raise ValidationError("Application history is immutable.")
        if self.revision.status != "published" or self.snapshot != self.revision.snapshot:
            raise ValidationError("Use an exact published revision snapshot.")
        self.full_clean()
        return super().save(*args, **kwargs)
