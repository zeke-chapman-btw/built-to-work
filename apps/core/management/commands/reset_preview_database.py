"""Wipe the PREVIEW database and start it fresh, optionally loading SAMPLE data.

Refuses anywhere except the password-gated preview site (see is_preview_site) and
requires the preview's own web address typed as confirmation. There is no undo.
"""
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.sample_data import is_preview_site, load_samples


class Command(BaseCommand):
    help = "Wipe the preview database, rebuild it, and (with --load-samples) add SAMPLE data."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", required=True, help="Type the preview web address, e.g. btw-preview.onrender.com")
        parser.add_argument("--load-samples", action="store_true", help="Load SAMPLE data after the wipe.")

    def handle(self, *args, **options):
        if not is_preview_site():
            raise CommandError("Refused: this only runs on the password-gated preview site.")
        hosts = [h.lower().lstrip(".") for h in settings.ALLOWED_HOSTS]
        if options["confirm"].strip().lower() not in hosts:
            raise CommandError("Refused: --confirm must be this site's web address (" + ", ".join(hosts) + ").")
        if connection.vendor != "postgresql":
            raise CommandError("Refused: the preview database is expected to be PostgreSQL.")
        self.stdout.write("Wiping the preview database...")
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
        connection.close()
        self.stdout.write("Rebuilding tables...")
        call_command("migrate", interactive=False, verbosity=0)
        self.stdout.write(self.style.SUCCESS("Preview database is empty and ready."))
        if options["load_samples"]:
            result, credentials = load_samples(allow_preview=True)
            self.stdout.write(result)
            for username, password in credentials:
                self.stdout.write(f"{username}: {password}")
            if credentials:
                self.stdout.write("Passwords are shown once. Save them somewhere private.")
