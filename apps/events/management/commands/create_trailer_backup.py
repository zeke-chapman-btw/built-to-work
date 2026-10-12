from django.core.management.base import BaseCommand, CommandError
from apps.events.backup_service import create_database_backup
from apps.events.models import TrailerInstance


class Command(BaseCommand):
    help = "Create an encrypted snapshot of the local SQLite trailer database."

    def add_arguments(self, parser):
        parser.add_argument("--trailer", required=True, help="TrailerInstance identity.")

    def handle(self, *args, **options):
        try:
            trailer = TrailerInstance.objects.get(identity=options["trailer"], is_active=True)
            record = create_database_backup(trailer)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Encrypted backup created: {record.pk}"))
