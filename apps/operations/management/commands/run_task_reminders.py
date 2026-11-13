import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Run one bounded reminder tick, or inspect scheduler health. Never sends email or SMS.'

    def add_arguments(self, parser):
        parser.add_argument('--status', action='store_true')
        parser.add_argument('--max-jobs', type=int, default=100)

    def handle(self, *args, **options):
        from apps.operations.models import TaskReminderSchedulerState, TaskReminderJob
        from apps.operations.reminder_preferences import enabled
        from apps.operations.reminders import run_tick
        if options['status']:
            state = TaskReminderSchedulerState.objects.filter(pk='task-reminders').values().first()
            age = ((timezone.now() - state['last_automatic_at']).total_seconds()
                   if state and state['last_automatic_at'] else None)
            self.stdout.write(json.dumps({'enabled': enabled(), 'automatic_healthy': age is not None and 0 <= age < 150 and not state['last_error'],
                'automatic_age_seconds': round(age, 1) if age is not None else None,
                'jobs_by_state': {key:TaskReminderJob.objects.filter(state=key).count() for key in TaskReminderJob.State.values},
                'state': state}, default=str))
            return
        if not enabled():
            raise CommandError('Task reminders are disabled in this environment.')
        if not 1 <= options['max_jobs'] <= 500:
            raise CommandError('--max-jobs must be between 1 and 500.')
        self.stdout.write(json.dumps(run_tick(max_jobs=options['max_jobs']), default=str))
