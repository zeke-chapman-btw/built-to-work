import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Run one bounded reminder tick, or inspect scheduler health. Never sends email or SMS.'

    def add_arguments(self, parser):
        parser.add_argument('--status', action='store_true')
        parser.add_argument('--max-jobs', type=int, default=100)

    def handle(self, *args, **options):
        from apps.operations.reminder_health import inspect_scheduler
        from apps.operations.reminder_preferences import enabled
        from apps.operations.reminders import run_tick
        if options['status']:
            self.stdout.write(json.dumps(inspect_scheduler(include_internal=True), default=str))
            return
        if not enabled():
            raise CommandError('Task reminders are disabled in this environment.')
        if not 1 <= options['max_jobs'] <= 500:
            raise CommandError('--max-jobs must be between 1 and 500.')
        self.stdout.write(json.dumps(run_tick(max_jobs=options['max_jobs']), default=str))
