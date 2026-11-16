"""Read shared messaging sign-ins: saved in the app first, Render settings second."""
from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError

from . import crypto

PURPOSE = "connection-secret"


def _row(key):
    from .models import ConnectionSetting
    try:
        return ConnectionSetting.objects.filter(key=key).first()
    except (DatabaseError, AssertionError, RuntimeError):
        # Table not migrated yet, or a context without database access (some tests).
        return None


def secret(row):
    return crypto.decrypt(row.secret_encrypted, PURPOSE) if row else ""


def save(*, key, values, actor, new_secret=None):
    from apps.core.audit import record_audit
    from .models import ConnectionSetting
    row, _ = ConnectionSetting.objects.get_or_create(key=key)
    before = {"values": row.values, "secret_saved": bool(row.secret_encrypted)}
    row.values = values
    if new_secret is not None:
        row.secret_encrypted = crypto.encrypt(new_secret, PURPOSE) if new_secret else ""
    row.updated_by = actor
    row.last_test_ok, row.last_test_at, row.last_test_detail = None, None, ""
    row.save()
    record_audit(actor=actor, action=f"messaging.connection.{key}.updated", instance=row, source="connections",
                 old_data=before, new_data={"values": row.values, "secret_saved": bool(row.secret_encrypted),
                                            "secret_changed": new_secret is not None},
                 reason="Connection settings changed in Settings → Connections.")
    return row


def record_test(key, ok, detail=""):
    from django.utils import timezone
    from .models import ConnectionSetting
    row, _ = ConnectionSetting.objects.get_or_create(key=key)
    row.last_test_ok, row.last_test_at, row.last_test_detail = ok, timezone.now(), (detail or "")[:240]
    row.save(update_fields=["last_test_ok", "last_test_at", "last_test_detail"])


def ez_credentials():
    """``(username, password, source)``; source is ``app``, ``render`` or ``""``."""
    row = _row("eztexting")
    if row and row.values.get("username") and row.secret_encrypted:
        return row.values["username"], secret(row), "app"
    username = getattr(settings, "EZTEXTING_USERNAME", "")
    password = getattr(settings, "EZTEXTING_PASSWORD", "")
    return (username, password, "render") if username and password else ("", "", "")


def email_settings():
    """Mail server and sender for automatic email; saved values win over Render's."""
    row = _row("email")
    values = row.values if row else {}
    saved_secret = secret(row) if row and row.secret_encrypted else ""
    from_address = values.get("from_address") or getattr(settings, "BTW_EMAIL_FROM", "") or getattr(settings, "BTW_TICKET_EMAIL_FROM", "")
    reply_to = values.get("reply_to")
    reply_to = [reply_to] if reply_to else list(getattr(settings, "BTW_EMAIL_REPLY_TO", []) or [])
    if values.get("username") and saved_secret:
        return {"source": "app", "host": values.get("host") or "smtp-relay.gmail.com", "port": int(values.get("port") or 587),
                "use_tls": True, "username": values["username"], "password": saved_secret,
                "from_address": from_address, "reply_to": reply_to}
    if getattr(settings, "EMAIL_HOST_USER", "") and getattr(settings, "EMAIL_HOST_PASSWORD", ""):
        return {"source": "render", "host": settings.EMAIL_HOST, "port": settings.EMAIL_PORT, "use_tls": settings.EMAIL_USE_TLS,
                "username": settings.EMAIL_HOST_USER, "password": settings.EMAIL_HOST_PASSWORD,
                "from_address": from_address, "reply_to": reply_to}
    return {"source": "", "from_address": from_address, "reply_to": reply_to}


def smtp_connection():
    """A real SMTP connection from the saved/Render sign-in, or ``None`` if not set up."""
    config = email_settings()
    if not config["source"]:
        return None
    from django.core.mail import get_connection
    return get_connection("django.core.mail.backends.smtp.EmailBackend", fail_silently=False, host=config["host"],
                          port=config["port"], username=config["username"], password=config["password"],
                          use_tls=config["use_tls"], timeout=20)


def automatic_sending_since():
    """When automatic messages were turned on, or ``None`` while they are off."""
    row = _row("sending")
    if not (row and row.values.get("enabled") and row.values.get("since")):
        return None
    from django.utils.dateparse import parse_datetime
    return parse_datetime(row.values["since"])


def automatic_sending_enabled():
    return automatic_sending_since() is not None
