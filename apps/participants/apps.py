from django.apps import AppConfig

class ParticipantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.participants"
    label = "participants"
    verbose_name = "Participants"

    def ready(self):
        from . import qualification_signals, intake_signals
