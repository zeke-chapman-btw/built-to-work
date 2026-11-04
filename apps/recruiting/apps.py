from django.apps import AppConfig


class RecruitingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.recruiting'
    verbose_name = 'Recruiting'

    def ready(self):
        from . import signals  # noqa: F401
