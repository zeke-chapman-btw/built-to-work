"""Two-step sign-in for staff, error alert settings and public legal pages."""
from django.conf import settings
from django.db import models


class SecuritySettings(models.Model):
    require_two_step_managers = models.BooleanField(default=False)
    error_alerts = models.BooleanField(default=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    @classmethod
    def current(cls):
        try:
            row, _ = cls.objects.get_or_create(pk=1)
        except Exception:
            return cls(pk=1)
        return row


class TwoStepDevice(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="two_step")
    secret_encrypted = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_step = models.BigIntegerField(default=0, help_text="Stops the same code being used twice.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class RecoveryCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()


class LegalPage(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=120)
    body = models.TextField()
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()


class ErrorAlert(models.Model):
    """One row per kind of error, so the same problem emails at most once an hour."""
    signature = models.CharField(max_length=64, unique=True)
    summary = models.CharField(max_length=300)
    path = models.CharField(max_length=300, blank=True)
    count = models.PositiveIntegerField(default=0)
    first_at = models.DateTimeField(auto_now_add=True)
    last_at = models.DateTimeField(auto_now=True)
    last_emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-last_at",)
        default_permissions = ()
