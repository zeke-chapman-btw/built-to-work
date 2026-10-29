from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    def ready(self):
        from django.db.models.signals import m2m_changed
        from .models import User
        from .signals import clear_role_cache
        m2m_changed.connect(clear_role_cache,sender=User.groups.through,dispatch_uid='staff-role-cache')
