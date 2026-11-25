from .base import *  # noqa: F403

SECRET_KEY = "django-insecure-test-only"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
BTW_ERROR_ALERTS = False
BTW_REMINDERS_MASTER_SWITCH = False  # tests turn reminder features on directly

# Page-view counting is switched on only in the tests that check it.
USAGE_TRACKING = False

# Portal views stay off in tests unless a test turns them on (the older tests check the preview-site-only switch).
BTW_PORTAL_VIEWS_ENABLED = False

# Sign-in pop-up messages stay off in tests unless a test turns them on (they add a form to every page).
SIGN_IN_MESSAGES_ENABLED = False
PAGE_HELP_ENABLED = False  # tests that need the "?" help link turn it on
