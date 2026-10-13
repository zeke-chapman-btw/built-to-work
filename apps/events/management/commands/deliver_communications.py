"""Process durable ticket and operational-alert queues."""
import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from apps.events.communications import deliver_alert, deliver_ticket, due_deliveries
from apps.events.models import OperationalNotificationDelivery, TicketDelivery


class Command(BaseCommand):
    help = "Send due ticket messages and operational alerts; --loop runs continuously."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep polling for restored connectivity")
        parser.add_argument("--poll-seconds", type=int, default=30)

    def handle(self, *args, **options):
        if options["poll_seconds"] < 1:
            raise ValueError("Poll interval must be positive")
        try:
            while True:
                close_old_connections()
                for model, deliver in ((TicketDelivery, deliver_ticket), (OperationalNotificationDelivery, deliver_alert)):
                    for pk in list(due_deliveries(model)[:100]):
                        result = deliver(pk)
                        if result:
                            self.stdout.write(f"{model.__name__} {pk}: {result.status}")
                if not options["loop"]:
                    return
                time.sleep(options["poll_seconds"])
        except KeyboardInterrupt:
            return
