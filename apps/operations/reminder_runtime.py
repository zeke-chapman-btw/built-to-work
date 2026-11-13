"""Bounded worker lifecycle on the existing, approved Preview web service."""
import logging
import os
import threading
import uuid

from django.conf import settings
from django.db import close_old_connections, connections

log = logging.getLogger(__name__)
PREVIEW_SERVICE = 'srv-db8n044s728c73aaet00'
_stop = threading.Event()
_thread = None


def permitted():
    preview = (os.environ.get('RENDER_SERVICE_ID') == PREVIEW_SERVICE and
               settings.BTW_PREVIEW_GATE_ENABLED and
               settings.EMAIL_BACKEND == 'django.core.mail.backends.locmem.EmailBackend')
    local = (settings.DEBUG and not os.environ.get('RENDER_SERVICE_ID') and
             os.environ.get('BTW_TASK_REMINDERS_LOCAL_WORKER') == 'true')
    return bool(settings.BTW_TASK_REMINDERS_ENABLED and (preview or local))


def _work(interval, worker_id):
    from .reminders import run_tick
    log.warning('TASK_REMINDER_WORKER_STARTED worker=%s interval=%s', worker_id, interval)
    while not _stop.is_set():
        close_old_connections()
        try:
            outcome = run_tick(worker_id=worker_id, automatic=True)
            log.info('TASK_REMINDER_AUTOMATIC_TICK worker=%s processed=%s', worker_id, outcome['processed'])
        except Exception:
            log.exception('TASK_REMINDER_AUTOMATIC_TICK_FAILED worker=%s', worker_id)
        finally:
            close_old_connections()
        _stop.wait(interval)


def start():
    global _thread
    if not permitted() or (_thread and _thread.is_alive()):
        return False
    # Configure before Gunicorn accepts requests or creates request threads.
    # Deferred SQLite read-to-write upgrades otherwise fail without waiting,
    # even with a busy timeout. This applies only to the opted-in local worker.
    database = settings.DATABASES['default']
    if database['ENGINE'] == 'django.db.backends.sqlite3':
        options = dict(database.get('OPTIONS', {}), transaction_mode='IMMEDIATE')
        options.setdefault('timeout', 30)
        database['OPTIONS'] = options
        connections['default'].close()
        connections['default'].settings_dict['OPTIONS'] = options
    _stop.clear()
    interval = 60
    if settings.DEBUG:
        interval = max(1, min(60, int(os.environ.get('BTW_TASK_REMINDERS_LOCAL_INTERVAL', '60'))))
    worker_id = 'gunicorn-%s-%s' % (os.getpid(), uuid.uuid4().hex[:12])
    _thread = threading.Thread(target=_work, args=(interval, worker_id), daemon=True,
                               name='task-reminders')
    _thread.start()
    return True


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=3)
