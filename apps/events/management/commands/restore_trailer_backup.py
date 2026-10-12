from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from apps.events.backup_service import restore_to_replacement
from apps.events.models import BackupRecord


class Command(BaseCommand):
    help = "Restore an encrypted backup to a new replacement SQLite database file."

    def add_arguments(self, parser):
        parser.add_argument("--backup-id", required=True)
        parser.add_argument("--target", required=True)
        parser.add_argument("--actor", required=True, help="Superuser username/email for the audit record.")
        parser.add_argument("--confirm", action="store_true", help="Confirm a new destination file will be created.")

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Pass --confirm after verifying the replacement target path.")
        user_model = get_user_model()
        try:
            actor = user_model._default_manager.get_by_natural_key(options["actor"])
            if not actor.is_superuser:
                raise CommandError("Restore requires a superuser account.")
            backup = BackupRecord.objects.get(pk=options["backup_id"], status=BackupRecord.Status.SUCCEEDED)
            target = restore_to_replacement(backup, options["target"], actor)
        except (BackupRecord.DoesNotExist, user_model.DoesNotExist) as exc:
            raise CommandError("Backup or actor account was not found.") from exc
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Restored verified backup to new file: {target}"))
