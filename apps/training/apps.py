from django.apps import AppConfig


class TrainingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.training"
    verbose_name = "Training"

    def ready(self):
        from . import signals  # noqa: F401
