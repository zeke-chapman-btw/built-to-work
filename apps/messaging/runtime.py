"""Background sender for queued ticket messages and alerts, inside the web service.

Runs only while an administrator has turned on "Automatic messages" in
Settings → Connections. Each pass sends due messages, then asks EZ Texting which
texts were delivered. Rows are claimed in the database, so a second process can
never send the same message twice.
"""
import logging
import os
import threading

from django.db import close_old_connections

log = logging.getLogger(__name__)
INTERVAL = 60
_stop = threading.Event()
_thread = None


def permitted():
    # Off for local development unless asked for; on for Render services.
    if os.environ.get("BTW_MESSAGE_WORKER", "").lower() == "false":
        return False
    return bool(os.environ.get("RENDER_SERVICE_ID")) or os.environ.get("BTW_MESSAGE_WORKER", "").lower() == "true"


def run_pass(limit=100):
    from apps.events.communications import deliver_alert, deliver_ticket, due_deliveries
    from apps.events.models import OperationalNotificationDelivery, TicketDelivery
    from .store import automatic_sending_since
    from .text_status import check_recent
    since = automatic_sending_since()
    if since is None:
        return {"sent": 0, "checked": 0, "enabled": False}
    handled = 0
    for model, deliver in ((TicketDelivery, deliver_ticket), (OperationalNotificationDelivery, deliver_alert)):
        # Only messages queued after sending was turned on; an old backlog never goes out by surprise.
        for pk in list(due_deliveries(model).filter(created_at__gte=since)[:limit]):
            if deliver(pk):
                handled += 1
    checked = check_recent()
    return {"sent": handled, "checked": checked, "enabled": True}


_last_bounce_check = [None]


def run_bounces(every_seconds=600):
    """Every 10 minutes, look for "couldn't deliver" notices in noreply@."""
    import time
    from .bounces import check_inbox
    now = time.monotonic()
    if _last_bounce_check[0] is not None and now - _last_bounce_check[0] < every_seconds:
        return 0
    _last_bounce_check[0] = now
    return check_inbox()


_last_reminders = [None]


def run_reminders(every_seconds=300):
    """Every 5 minutes, send reminders that are due (Settings → Reminders)."""
    import time
    from apps.reminders.engine import run_pass as reminders_pass
    now = time.monotonic()
    if _last_reminders[0] is not None and now - _last_reminders[0] < every_seconds:
        return None
    _last_reminders[0] = now
    return reminders_pass()


def run_mailchimp():
    from .mailchimp import sync_pending
    return sync_pending()


def _work():
    while not _stop.is_set():
        close_old_connections()
        try:
            run_mailchimp()
        except Exception:
            log.exception("MAILCHIMP_SYNC_FAILED")
        try:
            from .email_replies import check_recent
            check_recent()
        except Exception:
            log.exception("GMAIL_REPLIES_FAILED")
        try:
            from .cultureindex import run_nightly
            run_nightly()
        except Exception:
            log.exception("CULTURE_INDEX_SYNC_FAILED")
        try:
            run_bounces()
        except Exception:
            log.exception("BOUNCE_CHECK_ERROR")
        try:
            run_reminders()
        except Exception:
            log.exception("REMINDERS_PASS_FAILED")
        try:
            outcome = run_pass()
            if outcome["sent"] or outcome["checked"]:
                log.info("MESSAGE_WORKER_PASS sent=%s checked=%s", outcome["sent"], outcome["checked"])
        except Exception:
            log.exception("MESSAGE_WORKER_PASS_FAILED")
        finally:
            close_old_connections()
        _stop.wait(INTERVAL)


def start():
    global _thread
    if not permitted() or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_work, daemon=True, name="message-sender")
    _thread.start()
    return True


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=3)
