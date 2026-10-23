import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.environ.get("SECRET_KEY", "")
DEBUG = False
ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()
]

INSTALLED_APPS = [
    "apps.stations.apps.StationsConfig",
    "apps.assessments.apps.AssessmentsConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.staffing.apps.StaffingConfig",
    "apps.operations.apps.OperationsConfig",
    "apps.workdays.apps.WorkdaysConfig",
    "apps.event_reports.apps.EventReportsConfig",
    'apps.participants.apps.ParticipantsConfig',
    'apps.events.apps.EventsConfig',
    "apps.games.apps.GamesConfig",
    "apps.simulators.apps.SimulatorsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.PreviewAccessMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
"apps.internal_ui.context_processors.workspace_navigation",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_ENGINE = os.environ.get("DB_ENGINE", "sqlite").lower()
if DATABASE_URL:
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=True)}
elif DATABASE_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("DB_NAME", str(BASE_DIR / "db.sqlite3")),
        }
    }
elif DATABASE_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", ""),
            "USER": os.environ.get("DB_USER", ""),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }
else:
    raise ValueError("DB_ENGINE must be 'sqlite' or 'postgresql'.")

TEST_RUNNER = "apps.core.test_runner.ProjectTestRunner"

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_CONSENT_ROOT = Path(os.environ.get("PRIVATE_CONSENT_ROOT", BASE_DIR / "private_consent_documents"))
PRIVATE_DOCUMENT_ROOT = Path(os.environ.get("PRIVATE_DOCUMENT_ROOT", BASE_DIR / "private_media" / "participant_documents"))
BTW_PREVIEW_GATE_ENABLED = os.environ.get("BTW_PREVIEW_GATE_ENABLED", "false").lower() == "true"
BTW_PREVIEW_GATE_USERNAME = os.environ.get("BTW_PREVIEW_GATE_USERNAME", "")
BTW_PREVIEW_GATE_PASSWORD = os.environ.get("BTW_PREVIEW_GATE_PASSWORD", "")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTHENTICATION_BACKENDS = [
    "apps.participants.auth.ParticipantAuthenticationBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# The local capture API is disabled until a private LAN helper token is configured.
SIMULATOR_INGESTION_TOKEN = os.environ.get("SIMULATOR_INGESTION_TOKEN", "")
SIMULATOR_TEST_IDENTIFIER = os.environ.get("SIMULATOR_TEST_IDENTIFIER", "0000000000")

# Trailer communications use deployment-provided providers. No real sends occur
# until the sender and transport settings are configured.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
PARTICIPANT_PORTAL_BASE_URL = os.environ.get('PARTICIPANT_PORTAL_BASE_URL', os.environ.get('RENDER_EXTERNAL_URL', ''))
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "false").lower() == "true"
BTW_TICKET_EMAIL_FROM = os.environ.get("BTW_TICKET_EMAIL_FROM", "")
BTW_SMS_PROVIDER_URL = os.environ.get("BTW_SMS_PROVIDER_URL", "")
BTW_SMS_PROVIDER_TOKEN = os.environ.get("BTW_SMS_PROVIDER_TOKEN", "")
BTW_CENTRAL_SYNC_URL = os.environ.get("BTW_CENTRAL_SYNC_URL", "")

PRIVATE_HISTORICAL_ROOT = Path(os.environ.get("PRIVATE_HISTORICAL_ROOT", BASE_DIR / "private_historical_imports"))
