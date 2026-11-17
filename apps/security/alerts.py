"""Email administrators when the app hits an error (at most once an hour per kind of error)."""
import hashlib
import logging
import threading
import traceback
from datetime import timedelta

from django.conf import settings


class AdminAlertHandler(logging.Handler):
    def emit(self, record):
        if not getattr(settings, "BTW_ERROR_ALERTS", False) or getattr(_local, "busy", False):
            return
        try:
            exc = record.exc_info[1] if record.exc_info else None
            kind = type(exc).__name__ if exc else record.levelname
            where = ""
            if record.exc_info and record.exc_info[2]:
                last = traceback.extract_tb(record.exc_info[2])[-1]
                where = f"{last.filename.rsplit('/', 2)[-1]}:{last.lineno}"
            request = getattr(record, "request", None)
            path = getattr(request, "path", "") or ""
            summary = f"{kind}: {str(exc or record.getMessage())[:200]}"
            signature = hashlib.sha256(f"{kind}|{where}|{record.name}".encode()).hexdigest()[:40]
            details = "".join(traceback.format_exception(*record.exc_info))[-4000:] if record.exc_info else record.getMessage()
        except Exception:
            return
        threading.Thread(target=_record, args=(signature, summary, path, details), daemon=True).start()


_local = threading.local()


def _record(signature, summary, path, details):
    from django.db import close_old_connections
    _local.busy = True
    try:
        from django.utils import timezone
        from .models import ErrorAlert, SecuritySettings
        if not SecuritySettings.current().error_alerts:
            return
        row, _ = ErrorAlert.objects.get_or_create(signature=signature, defaults={"summary": summary[:300], "path": path[:300]})
        row.count += 1
        row.summary, row.path = summary[:300], path[:300]
        now = timezone.now()
        send = not row.last_emailed_at or now - row.last_emailed_at > timedelta(hours=1)
        if send:
            row.last_emailed_at = now
        row.save()
        if send:
            _email(row, details)
    except Exception:
        pass
    finally:
        _local.busy = False
        close_old_connections()


def _email(row, details):
    from django.contrib.auth import get_user_model
    from django.core.mail import send_mail
    from apps.messaging import store
    to = list(get_user_model().objects.filter(is_active=True, is_superuser=True).exclude(email="").values_list("email", flat=True))
    if not to:
        return
    site = (getattr(settings, "PARTICIPANT_PORTAL_BASE_URL", "") or "the app").rstrip("/")
    body = (f"Built to Work hit an error on {site}.\n\nWhat: {row.summary}\nPage: {row.path or '—'}\n"
            f"Times so far: {row.count}\n\nYou'll get at most one email an hour for this same error. "
            f"Settings → Security lists recent errors.\n\nTechnical details (for whoever fixes it):\n{details}")
    send_mail(f"[BTW] App error: {row.summary[:80]}", body, store.email_settings().get("from_address") or None, to,
              fail_silently=True, connection=store.smtp_connection())
