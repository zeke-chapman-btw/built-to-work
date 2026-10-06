import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

class GameDefinition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=80, unique=True)
    display_name = models.CharField(max_length=120)
    implementation_key = models.SlugField(max_length=80)
    active = models.BooleanField(default=True)
    implementation_version = models.CharField(max_length=40, default='1')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ('display_name',)
    def __str__(self): return self.display_name

class EventGameConfiguration(models.Model):
    event = models.OneToOneField('events.Event', on_delete=models.CASCADE, related_name='game_configuration')
    game = models.ForeignKey(GameDefinition, on_delete=models.PROTECT, related_name='event_configurations')
    configuration_version = models.CharField(max_length=40, default='1')
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def clean(self):
        if self.game_id and not self.game.active: raise ValidationError({'game': 'Choose an active Game Type.'})
    def __str__(self): return f'{self.event}: {self.game}'

class GameSession(models.Model):
    class Mode(models.TextChoices):
        OFFICIAL = 'official', 'Official'
        STAFF_TEST = 'staff_test', 'Staff test'
        DEMO = 'demo', 'Demo'
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Not started'
        IN_PROGRESS = 'in_progress', 'In progress'
        COMPLETED = 'completed', 'Completed'
        VOIDED = 'voided', 'Voided'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey('events.Event', on_delete=models.PROTECT, related_name='game_sessions')
    experience_session = models.ForeignKey('stations.ExperienceSession', on_delete=models.PROTECT, related_name='game_sessions')
    game = models.ForeignKey(GameDefinition, on_delete=models.PROTECT, related_name='sessions')
    mode = models.CharField(max_length=20, choices=Mode.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    game_version = models.CharField(max_length=40)
    configuration_version = models.CharField(max_length=40)
    configuration_snapshot = models.JSONField(default=dict, blank=True)
    raw_score = models.PositiveIntegerField(null=True, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='voided_game_sessions')
    void_reason = models.CharField(max_length=500, blank=True)
    replaced_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replaces')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ('created_at',)
        constraints = [models.UniqueConstraint(fields=('experience_session', 'game'), condition=~Q(status='voided'), name='games_one_accepted_session_per_experience_game')]
        indexes = [models.Index(fields=('event', 'mode', 'status'), name='games_event_mode_status')]
    def clean(self):
        if self.experience_session_id and self.event_id and self.experience_session.event_id != self.event_id:
            raise ValidationError('Game and experience session must belong to the same Event.')
        if self.experience_session_id and self.mode != self.experience_session.mode:
            raise ValidationError('Game Session mode must match its Experience Session.')
    @property
    def is_official(self): return self.mode == self.Mode.OFFICIAL
    def __str__(self): return f'{self.game.display_name} — {self.status}'
