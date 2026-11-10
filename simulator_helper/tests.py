"""Hardware-free tests: python -m unittest simulator_helper.tests -v."""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

from .calibration import rectangle_from_drag, save_region
from .core import (CaptureEngine, ConfigurationError, HelperConfig, Phase, Profile,
                   Reading, Rectangle, StateStore, authorization_header,
                   parse_identifier, parse_score)
from .windows import read_region


PROFILE_ID = str(uuid.uuid4())
EVENT_ID = str(uuid.uuid4())
CONTEXT = {"left": -100, "top": 0, "width": 1000, "height": 600,
           "monitors": [{"left": -100, "top": 0, "width": 1000, "height": 600}]}


def profile_data():
    return {"profile_id": PROFILE_ID, "profile_version": "7", "expected_width": 1000,
            "expected_height": 600, "identifier_region": {"x": 10, "y": 10, "width": 200, "height": 40},
            "score_region": {"x": 700, "y": 10, "width": 200, "height": 50},
            "state_regions": [], "known_identifier_prefixes": ["tel"], "display_context": CONTEXT}


class HelperCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = Profile.from_dict(profile_data())
        self.config = HelperConfig("http://localhost:8000", EVENT_ID, "SIM-01", self.profile,
                                   "local-token", 0.75, 3, 70, self.root / "state.json")
        self.responses = []
        self.calls = []

        def post(payload):
            self.calls.append(dict(payload))
            return self.responses.pop(0) if self.responses else (201, {"outcome": "matched"})

        self.engine = CaptureEngine(self.config, StateStore(self.config.state_path), post)
        self.post = post

    def clear_screen(self, engine=None):
        engine = engine or self.engine
        for _ in range(3):
            engine.observe_score_clear(Reading("", 0))

    def capture_identifier(self, value="tel4785551234", engine=None):
        engine = engine or self.engine
        self.clear_screen(engine)
        for _ in range(3):
            engine.observe_identifier(Reading(value, 95))

    def capture_score(self, value="83.33%", engine=None):
        engine = engine or self.engine
        for _ in range(3):
            engine.observe_score(Reading("", 0))
        for _ in range(3):
            engine.observe_score(Reading(value, 95))

    def test_normal_flow_and_reset(self):
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_ID)
        self.capture_identifier()
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_SCORE)
        self.capture_score()
        self.assertEqual(self.engine.phase, Phase.SUBMITTING)
        payload = self.engine.pending
        self.assertEqual(payload["station_code"], "SIM-01")
        self.assertEqual(payload["profile_version"], "7")
        self.assertEqual(payload["raw_total_score"], "83.33%")
        self.engine.submit_once()
        self.assertEqual(self.engine.phase, Phase.RESET)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.engine.observe_identifier(Reading("tel0000000000", 95)))
        self.clear_screen()
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_ID)

    def test_identifier_and_score_need_stability(self):
        self.clear_screen()
        self.assertFalse(self.engine.observe_identifier(Reading("tel4785551234", 95)))
        self.assertFalse(self.engine.observe_identifier(Reading("tel4785559999", 95)))
        self.assertFalse(self.engine.observe_identifier(Reading("tel4785551234", 95)))
        self.assertFalse(self.engine.observe_identifier(Reading("tel4785551234", 95)))
        self.assertTrue(self.engine.observe_identifier(Reading("tel4785551234", 95)))
        for _ in range(3):
            self.assertFalse(self.engine.observe_score(Reading("", 0)))
        self.assertFalse(self.engine.observe_score(Reading("83.33%", 95)))
        self.assertFalse(self.engine.observe_score(Reading("84%", 95)))
        self.assertFalse(self.engine.observe_score(Reading("83.33%", 95)))
        self.assertFalse(self.engine.observe_score(Reading("83.33%", 95)))
        self.assertTrue(self.engine.observe_score(Reading("83.33%", 95)))

    def test_no_score_before_identifier_or_before_blank(self):
        for _ in range(5):
            self.assertFalse(self.engine.observe_score(Reading("100%", 99)))
            self.assertFalse(self.engine.observe_score_clear(Reading("100%", 99)))
        self.assertFalse(self.engine.ready_for_id)
        self.capture_identifier()
        for _ in range(5):
            self.assertFalse(self.engine.observe_score(Reading("100%", 99)))
        self.assertIsNone(self.engine.pending)

    def test_retry_and_restart_keep_submission_id(self):
        self.capture_identifier()
        self.capture_score()
        submission = self.engine.pending["submission_id"]
        self.responses = [(0, {"error": "network_unavailable"}), (201, {"outcome": "matched"})]
        self.engine.submit_once()
        self.assertEqual(self.engine.phase, Phase.SUBMITTING)
        restarted = CaptureEngine(self.config, StateStore(self.config.state_path), self.post)
        self.assertEqual(restarted.pending["submission_id"], submission)
        restarted.submit_once()
        self.assertEqual([call["submission_id"] for call in self.calls], [submission, submission])
        self.assertEqual(restarted.phase, Phase.RESET)

    def test_rejection_preserves_pending(self):
        self.capture_identifier()
        self.capture_score()
        self.responses = [(422, {"error": "invalid_capture"})]
        self.engine.submit_once()
        self.assertEqual(self.engine.phase, Phase.SUBMITTING)
        self.assertIsNotNone(StateStore(self.config.state_path).read()["pending"])

    def test_stale_score_cannot_pair_with_next_identifier(self):
        self.capture_identifier()
        self.capture_score()
        self.engine.submit_once()
        for _ in range(4):
            self.assertFalse(self.engine.observe_score_clear(Reading("83.33%", 95)))
        self.assertEqual(self.engine.phase, Phase.RESET)
        self.clear_screen()
        self.capture_identifier("tel0000000000")
        self.capture_score("0%")
        self.assertEqual(self.engine.pending["raw_identifier"], "tel0000000000")
        self.assertEqual(self.engine.pending["raw_total_score"], "0%")
        self.assertNotEqual(self.calls[0]["submission_id"], self.engine.pending["submission_id"])

    def test_parsers_are_conservative(self):
        self.assertEqual(parse_identifier("tel4785551234"), "tel4785551234")
        self.assertEqual(parse_identifier("478 555 1234"), "4785551234")
        self.assertEqual(parse_identifier("tel0000000000"), "tel0000000000")
        self.assertIsNone(parse_identifier("PASS4785551234"))
        for raw in ("83.33%", "83.33", "100%", "0%"):
            self.assertIsNotNone(parse_score(raw))
        for raw in ("O%", "101%", "83..3%", "PASS", "83.333%"):
            self.assertIsNone(parse_score(raw))
        self.clear_screen()
        for _ in range(5):
            self.engine.observe_identifier(Reading("tel47B5551234", 90))
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_ID)

    def test_config_and_auth_validation(self):
        (self.root / "profile.json").write_text(json.dumps(profile_data()))
        (self.root / "config.json").write_text(json.dumps({
            "server_url": "http://localhost:8000", "event_id": EVENT_ID,
            "station_code": "SIM-01", "stable_reads": 3,
        }))
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                HelperConfig.load(self.root / "config.json")
            self.assertEqual(HelperConfig.load(self.root / "config.json", require_token=False).profile.profile_version, "7")
        with mock.patch.dict(os.environ, {"SIMULATOR_INGESTION_TOKEN": "secret"}):
            self.assertEqual(HelperConfig.load(self.root / "config.json").token, "secret")
        self.assertEqual(authorization_header("secret"), "Bearer secret")
        with self.assertRaises(ConfigurationError):
            authorization_header("bad token")
        with self.assertRaises(ConfigurationError):
            Rectangle.from_dict({"x": 990, "y": 0, "width": 20, "height": 10}, 1000, 600)

    def test_rectangle_calibration_and_profile_loading(self):
        rect = rectangle_from_drag((100, 50), (200, 100), 2, 2, 1000, 600)
        self.assertEqual(rect.as_dict(), {"x": 200, "y": 100, "width": 200, "height": 100})
        with self.assertRaises(ConfigurationError):
            rectangle_from_drag((100, 100), (100, 100), 2, 2, 1000, 600)
        exported = save_region(self.root / "profile.json", profile_data(), "identifier", rect, CONTEXT)
        self.assertEqual(Profile.from_dict(exported).identifier_region, rect)
        self.assertNotEqual(exported["profile_version"], "7")

    def test_ocr_accepts_synthetic_region_without_desktop(self):
        class FakeImage:
            width, height = 200, 50
            def resize(self, size):
                return self
            def save(self, stream, format):
                stream.write(b"fake image")
        image_ops = types.SimpleNamespace(grayscale=lambda image: image, autocontrast=lambda image: image)
        fake_pil = types.SimpleNamespace(ImageOps=image_ops)
        tsv = (chr(9).join(("level", "conf", "text")) + chr(10) + chr(9).join(("5", "93", "83.33%")) + chr(10)).encode()
        completed = types.SimpleNamespace(returncode=0, stdout=tsv)
        with mock.patch.dict("sys.modules", {"PIL": fake_pil}), mock.patch("subprocess.run", return_value=completed):
            reading = read_region(FakeImage(), "score")
        self.assertEqual(reading.text, "83.33%")
        self.assertEqual(reading.confidence, 93)


    def test_last_submission_status_survives_reset_and_restart(self):
        self.capture_identifier()
        self.capture_score()
        self.engine.submit_once()
        self.assertEqual(StateStore(self.config.state_path).read()["last_result"]["http_status"], 201)
        self.clear_screen()
        restarted = CaptureEngine(self.config, StateStore(self.config.state_path), self.post)
        self.assertEqual(restarted.last_result["outcome"], "matched")
        self.assertEqual(restarted.phase, Phase.WAITING_FOR_ID)


if __name__ == "__main__":
    unittest.main()
