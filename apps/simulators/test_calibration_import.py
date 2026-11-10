import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import SimulatorCaptureProfile


class CalibrationImportTests(TestCase):
    def setUp(self):
        self.profile = SimulatorCaptureProfile.objects.create(
            name="SimU review", simulator_type="simu", profile_version="1"
        )
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profile.json"
        self.payload = {
            "profile_id": str(self.profile.pk), "profile_version": "2",
            "expected_width": 1000, "expected_height": 600,
            "identifier_region": {"x": 10, "y": 10, "width": 200, "height": 40},
            "score_region": {"x": 700, "y": 10, "width": 200, "height": 50},
            "state_regions": [], "known_identifier_prefixes": ["tel"],
            "display_context": {"left": 0, "top": 0, "width": 1000, "height": 600, "monitors": []},
        }

    def save(self):
        self.path.write_text(json.dumps(self.payload))

    def test_import_updates_existing_profile_without_changing_event_context(self):
        self.save()
        call_command("import_simulator_calibration", self.path)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.profile_version, "2")
        self.assertEqual(self.profile.identifier_region["x"], 10)
        self.assertEqual(self.profile.score_region["x"], 700)
        self.assertEqual(self.profile.expected_width, 1000)

    def test_import_rejects_changed_calibration_without_new_version(self):
        self.profile.profile_version = "2"
        self.profile.save()
        self.save()
        with self.assertRaises(CommandError):
            call_command("import_simulator_calibration", self.path)

    def test_import_requires_existing_profile(self):
        import uuid
        self.payload["profile_id"] = str(uuid.uuid4())
        self.save()
        with self.assertRaises(CommandError):
            call_command("import_simulator_calibration", self.path)
