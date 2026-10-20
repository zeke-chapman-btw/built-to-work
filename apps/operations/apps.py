from django.apps import AppConfig
class OperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.operations"
    def ready(self):
        from . import notifications  # Register the new-audit-event receiver.
