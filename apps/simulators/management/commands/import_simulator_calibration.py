"""Apply a simulator PC calibration export on the trailer Django server."""
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.simulators.models import SimulatorCaptureProfile
from simulator_helper.core import ConfigurationError, Profile, load_json


class Command(BaseCommand):
    help = "Import a calibrated profile JSON from a simulator PC; never reads ingestion tokens"

    def add_arguments(self, parser):
        parser.add_argument("profile_json", type=Path)

    def handle(self, *args, **options):
        try:
            calibrated = Profile.from_dict(load_json(options["profile_json"]))
        except ConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        with transaction.atomic():
            try:
                profile = SimulatorCaptureProfile.objects.select_for_update().get(pk=calibrated.profile_id)
            except SimulatorCaptureProfile.DoesNotExist as exc:
                raise CommandError("Create the matching SimulatorCaptureProfile on the server first") from exc
            new_values = {
                "profile_version": calibrated.profile_version,
                "expected_width": calibrated.expected_width,
                "expected_height": calibrated.expected_height,
                "identifier_region": calibrated.identifier_region.as_dict(),
                "score_region": calibrated.score_region.as_dict(),
                "state_regions": [region.as_dict() for region in calibrated.state_regions],
                "known_identifier_prefixes": list(calibrated.known_identifier_prefixes),
            }
            if profile.profile_version == calibrated.profile_version and any(
                getattr(profile, key) != value for key, value in new_values.items()
            ):
                raise CommandError("Calibration changed without a new profile version")
            for key, value in new_values.items():
                setattr(profile, key, value)
            try:
                profile.full_clean()
            except ValidationError as exc:
                raise CommandError("Calibration failed server validation: " + "; ".join(exc.messages)) from exc
            profile.save(update_fields=[*new_values, "updated_at"])
        self.stdout.write(self.style.SUCCESS(
            f"Imported profile {profile.pk} version {profile.profile_version}; Event/Station configuration remains unchanged"
        ))
