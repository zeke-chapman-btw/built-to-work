from collections import Counter
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.participants.portal_accounts import process_deliveries
from apps.participants.models import PortalAccessAttempt
from datetime import timedelta


class Command(BaseCommand):
    help = 'Deliver due intake-bound portal invitations/reminders. Run periodically in the same trusted deployment environment.'
    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
    def handle(self, *args, **options):
        limit = min(max(options['limit'], 1), 500)
        counts = Counter(process_deliveries(limit=limit))
        PortalAccessAttempt.objects.filter(started_at__lt=timezone.now() - timedelta(days=2)).delete()
        self.stdout.write(str(dict(counts)))
