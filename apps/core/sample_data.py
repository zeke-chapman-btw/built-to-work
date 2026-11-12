"""On-demand, audited samples; never imported by a deploy or startup hook.

The audit manifest tracks exact primary keys for repeat-safe loading. Reset
preserves protected history and requires a fresh local database instead.
No passwords enter the manifest.
"""
import os
import threading
from collections import defaultdict
from contextlib import contextmanager

from django.apps import apps
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import FileField
from django.db.models.signals import post_save
from django.test.utils import override_settings

from apps.core.audit import record_audit
from apps.core.models import AuditLog

SOURCE = "sample_data_v1"
WHY = "SAMPLE screen-review data; fictional, no messages sent"
USERNAMES = ("SAMPLE_admin", "SAMPLE_event_lead", "SAMPLE_staff", "SAMPLE_staff_no_docs")


def require_safe_environment(allow_preview):
    modules = (getattr(settings, "SETTINGS_MODULE", ""), os.environ.get("DJANGO_SETTINGS_MODULE", ""))
    if any("production" in (value or "").lower().split(".") for value in modules):
        raise CommandError("Sample data is forbidden with production settings, including --allow-preview.")
    if not settings.DEBUG and not allow_preview:
        raise CommandError("Sample data requires DEBUG=True or explicit --allow-preview.")
    if os.environ.get("RENDER_SERVICE_ID") and not allow_preview:
        raise CommandError("A hosted environment requires explicit --allow-preview.")
    db = connection.settings_dict
    if db["ENGINE"] not in ("django.db.backends.sqlite3", "django.db.backends.postgresql"):
        raise CommandError("Sample data supports SQLite and PostgreSQL only.")
    if db["ENGINE"].endswith("postgresql") and db.get("HOST") not in ("", "localhost", "127.0.0.1", "::1") and not allow_preview:
        raise CommandError("A remote database requires explicit --allow-preview.")


@contextmanager
def capture_created():
    """Track this thread's successful inserts; this signal does NOT create audits."""
    owner_thread = threading.get_ident()
    owned = defaultdict(set)
    files = []

    def inserted(sender, instance, created, **kwargs):
        if not created or threading.get_ident() != owner_thread:
            return
        if sender._meta.label_lower in ("core.auditlog", "contenttypes.contenttype", "auth.permission"):
            return
        owned[sender._meta.label_lower].add(str(instance.pk))
        for field in instance._meta.fields:
            if isinstance(field, FileField):
                value = getattr(instance, field.name)
                if value and value.name:
                    files.append((sender._meta.label_lower, field.name, value.name))

    post_save.connect(inserted, weak=False)
    try:
        yield owned, files
    except BaseException:
        for label, field, name in files:
            apps.get_model(label)._meta.get_field(field).storage.delete(name)
        raise
    finally:
        post_save.disconnect(inserted)


def include_created_children(owned):
    """Include known service bulk inserts and implicit M2M join inserts.

    All other inserts emit post_save. Do not infer ownership of arbitrary
    business rows from an actor or other reference to a newly created user.
    """
    changed = True
    while changed:
        changed = False
        for model in apps.get_models(include_auto_created=True):
            label = model._meta.label_lower
            if not model._meta.auto_created and label not in ("staffing.assignmentday", "operations.eventchecklistitem"):
                continue
            for field in model._meta.fields:
                if not field.is_relation or not field.remote_field:
                    continue
                parent = field.remote_field.model._meta.label_lower
                if not owned.get(parent):
                    continue
                # Shared Permission / ContentType rows are never claimed.
                found = {str(pk) for pk in model._base_manager.filter(**{field.attname + "__in": owned[parent]}).values_list("pk", flat=True)}
                extra = found - owned[label]
                if extra:
                    owned[label].update(extra)
                    changed = True


def load_samples(*, allow_preview=False, reset=False):
    require_safe_environment(allow_preview)
    # Locmem prevents email delivery even if settings normally configure a
    # provider. All creation paths below are non-delivery domain operations.
    with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                           BTW_SMS_PROVIDER_URL="", BTW_SMS_PROVIDER_TOKEN=""):
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1734417017])
            latest = AuditLog.objects.filter(source=SOURCE, action__in=("sample_data.created", "sample_data.reset")).order_by("-timestamp", "-pk").first()
            active = latest if latest and latest.action == "sample_data.created" else None
            if reset:
                if active:
                    raise CommandError("SAMPLE data includes protected submitted job-position history. "
                                       "Nothing removed. Start a fresh local database to remove this dataset.")
                return "No active SAMPLE data to remove.", []
            if active:
                return "SAMPLE data already loaded; unchanged. Logins: " + ", ".join(USERNAMES), []
            from django.contrib.auth import get_user_model
            if get_user_model().objects.filter(username__in=USERNAMES).exists():
                raise CommandError("A SAMPLE username already exists without an active ownership manifest; nothing changed.")
            from apps.core.sample_data_seed import create_samples
            with capture_created() as (owned, files):
                credentials = create_samples()
                include_created_children(owned)
                record_audit(actor=None, action="sample_data.created", instance=get_user_model().objects.get(username=USERNAMES[0]), source=SOURCE,
                             new_data={"owned": {key: sorted(value) for key, value in owned.items() if value}, "files": files}, reason=WHY)
            return "SAMPLE data loaded: 25 people, 4 events, 2 customers and 4 staff logins.", credentials
