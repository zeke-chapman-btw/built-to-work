from django.core.management.base import BaseCommand

from apps.core.sample_data import load_samples


class Command(BaseCommand):
    help = "Create clearly labelled, offline SAMPLE data for local screen reviews."

    def add_arguments(self, parser):
        parser.add_argument("--allow-preview", action="store_true",
                            help="Allow non-production settings with DEBUG disabled.")
        parser.add_argument("--reset", action="store_true",
                            help="Check reset requirements; loaded sample history requires a fresh local database.")

    def handle(self, *args, **options):
        result, credentials = load_samples(allow_preview=options["allow_preview"], reset=options["reset"])
        self.stdout.write(result)
        for username, password in credentials:
            self.stdout.write(f"{username}: {password}")
        if credentials:
            self.stdout.write("Passwords are shown once. Keep this terminal private.")
