"""Gunicorn automatically reads this file from the application working directory."""


def post_worker_init(worker):
    from apps.operations.reminder_runtime import start
    start()


def worker_exit(server, worker):
    from apps.operations.reminder_runtime import stop
    stop()
