from django.core.management.base import BaseCommand
from apps.participants.system_test import get_or_create_test_participant


class Command(BaseCommand):
    help = "Provision the single permanent BTW test Participant (no Event or ticket is created)."

    def add_arguments(self, parser):
        parser.add_argument("--show-token", action="store_true",
            help="Display the reusable test QR credential in this trusted terminal.")

    def handle(self, *args, **options):
        participant = get_or_create_test_participant()
        self.stdout.write(f"BTW test Participant: {participant.identity_uuid}")
        if options["show_token"]:
            self.stdout.write(f"Reusable test QR value: {participant.test_qr_token}")
        else:
            self.stdout.write("Use --show-token in a trusted terminal when provisioning the scanner.")
