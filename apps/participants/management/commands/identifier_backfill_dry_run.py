import json
from django.core.management.base import BaseCommand
from apps.participants.identifier_dry_run import historical_dry_run

class Command(BaseCommand):
    help = 'Report historical identifier candidates without writing any data; no apply option.'
    def handle(self, *args, **options):
        self.stdout.write(json.dumps(historical_dry_run(), sort_keys=True, indent=2))
