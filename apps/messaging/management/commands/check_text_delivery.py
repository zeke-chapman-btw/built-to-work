from django.core.management.base import BaseCommand

from apps.messaging.text_status import check_recent


class Command(BaseCommand):
    help = "Ask EZ Texting whether recently sent texts were delivered."

    def handle(self, *args, **options):
        self.stdout.write(f"Checked {check_recent()} text(s).")
