"""Restore a trailer after total loss of its original SQLite database."""

import getpass
import sys

from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand, CommandError

from apps.events.portable_recovery import restore_portable_backup


class Command(BaseCommand):
    help = "Restore a new trailer SQLite database from an encrypted archive and manifest."

    def add_arguments(self, parser):
        parser.add_argument("--archive", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--target", required=True)
        parser.add_argument("--actor", required=True, help="Active superuser identity in the encrypted snapshot.")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--password-stdin", action="store_true", help="Read the admin password from standard input, not command arguments.")

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Pass --confirm after checking that the destination is a new, empty database path.")
        password = (sys.stdin.readline().rstrip("\r\n") if options["password_stdin"]
                    else getpass.getpass("Recovery administrator password: "))
        try:
            target = restore_portable_backup(
                archive_path=options["archive"], manifest_path=options["manifest"],
                target_path=options["target"], admin_identifier=options["actor"],
                admin_password=password,
            )
        except (PermissionError, ValueError, FileNotFoundError, FileExistsError, InvalidToken, NotImplementedError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Restored and audited replacement database: {target}"))
