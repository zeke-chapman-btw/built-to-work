from .base import *  # noqa: F403

SECRET_KEY = "django-insecure-test-only"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
BTW_ERROR_ALERTS = False
BTW_REMINDERS_MASTER_SWITCH = False  # tests turn reminder features on directly
