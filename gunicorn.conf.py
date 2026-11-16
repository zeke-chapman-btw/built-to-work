"""Gunicorn automatically reads this file from the application working directory."""


def post_worker_init(worker):
    from apps.operations.reminder_runtime import start
    start()
    from apps.messaging.runtime import start as start_messages
    start_messages()


def worker_exit(server, worker):
    from apps.operations.reminder_runtime import stop
    stop()
    from apps.messaging.runtime import stop as stop_messages
    stop_messages()
