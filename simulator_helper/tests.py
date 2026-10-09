"""Hardware-free tests: python -m unittest simulator_helper.tests -v."""
from __future__ import annotations

import json
import logging
import os
import types
import unittest
import uuid
import time
from dataclasses import replace
from pathlib import Path
from unittest import mock

from .calibration import rectangle_from_drag, save_region
from .core import (CaptureEngine, ConfigurationError, HelperConfig, Phase, Profile,
                   Reading, Rectangle, StateStore, authorization_header,
                   parse_identifier, parse_score)
from .windows import read_region
from .xml_results import (FileSignature, XMLDirectoryScanner, XMLMonitorWorker,
                          XMLResultRepository, identify_exercise, parse_scorecard)


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
        self.root = Path(__file__).resolve().parent
        self.test_prefix = f".test-{uuid.uuid4().hex}-"
        self.test_files = []
        self.profile = Profile.from_dict(profile_data())
        self.config = HelperConfig("http://localhost:8000", EVENT_ID, "SIM-01", self.profile,
                                   "test-token-placeholder", 0.75, 3, 70, self.artifact_path("state.json"))
        self.responses = []
        self.calls = []

        def post(payload):
            self.calls.append(dict(payload))
            return self.responses.pop(0) if self.responses else (201, {"outcome": "matched"})

        self.engine = CaptureEngine(self.config, StateStore(self.config.state_path), post)
        self.post = post

    def artifact_path(self, name):
        path = self.root / (self.test_prefix + name)
        self.test_files.append(path)
        return path

    def tearDown(self):
        for path in self.test_files:
            if path.exists():
                path.unlink()

    def clear_screen(self, engine=None):
        engine = engine or self.engine
        for _ in range(3):
            engine.observe_score_clear(Reading("", 0))

    def capture_identifier(self, value="tel0000000001", engine=None):
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

    def make_engine(self, now, timeout=300):
        config = replace(self.config, state_path=self.artifact_path(f"state-{timeout}.json"),
                         session_timeout_seconds=timeout)
        calls = []

        def post(payload):
            calls.append(dict(payload))
            return self.responses.pop(0) if self.responses else (201, {"outcome": "matched"})

        engine = CaptureEngine(config, StateStore(config.state_path), post, clock=lambda: now[0])
        return engine, calls

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
        self.assertFalse(self.engine.observe_identifier(Reading("tel0000000001", 95)))
        self.assertFalse(self.engine.observe_identifier(Reading("tel0000000002", 95)))
        self.assertFalse(self.engine.observe_identifier(Reading("tel0000000001", 95)))
        self.assertFalse(self.engine.observe_identifier(Reading("tel0000000001", 95)))
        self.assertTrue(self.engine.observe_identifier(Reading("tel0000000001", 95)))
        for _ in range(3):
            self.assertFalse(self.engine.observe_score(Reading("", 0)))
        self.assertFalse(self.engine.observe_score(Reading("83.33%", 95)))
        self.assertFalse(self.engine.observe_score(Reading("84%", 95)))
        self.assertFalse(self.engine.observe_score(Reading("83.33%", 95)))
        self.assertFalse(self.engine.observe_score(Reading("83.33%", 95)))
        self.assertTrue(self.engine.observe_score(Reading("83.33%", 95)))

    def test_identifier_has_separate_confidence_threshold(self):
        self.clear_screen()
        for _ in range(3):
            self.assertFalse(self.engine.observe_identifier(Reading("0000000003", 44)))
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_ID)
        for _ in range(2):
            self.assertFalse(self.engine.observe_identifier(Reading("0000000003", 48.7)))
        self.assertTrue(self.engine.observe_identifier(Reading("0000000003", 48.7)))
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_SCORE)
        for _ in range(3):
            self.engine.observe_score(Reading("", 0))
        for _ in range(3):
            self.assertFalse(self.engine.observe_score(Reading("83.33%", 48.7)))
        self.assertIsNone(self.engine.pending)

    def test_participant_change_discards_old_id_and_waits_for_fresh_score(self):
        self.capture_identifier()
        for _ in range(3):
            self.engine.observe_identifier(Reading("tel0000000004", 95))
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_ID)
        self.assertIsNone(self.engine.identifier)
        self.assertIsNone(self.engine.pending)
        self.assertFalse(self.engine.observe_score_clear(Reading("81.5%", 95)))
        self.assertFalse(self.engine.observe_identifier(Reading("tel0000000004", 95)))

        self.clear_screen()
        self.capture_identifier("tel0000000004")
        self.capture_score("72.5%")
        self.assertEqual(self.engine.pending["raw_identifier"], "tel0000000004")
        self.assertEqual(self.engine.pending["raw_total_score"], "72.5%")

    def test_score_visible_before_identifier_is_never_accepted(self):
        for _ in range(5):
            self.assertFalse(self.engine.observe_score_clear(Reading("84%", 95)))
            self.assertFalse(self.engine.observe_identifier(Reading("tel0000000001", 95)))
            self.assertFalse(self.engine.observe_score(Reading("84%", 95)))
        self.assertFalse(self.engine.ready_for_id)
        self.assertIsNone(self.engine.pending)

    def test_identifier_disappearance_does_not_abandon_session(self):
        self.capture_identifier()
        for _ in range(4):
            self.engine.observe_identifier(Reading("", 0))
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_SCORE)
        self.assertEqual(self.engine.identifier, "tel0000000001")
        self.assertFalse(self.engine.identifier_change_suspected)
        self.capture_score("81%")
        self.assertEqual(self.engine.pending["raw_identifier"], "tel0000000001")

    def test_fluctuating_identifier_quarantines_then_reconfirms_original(self):
        self.capture_identifier()
        self.engine.observe_identifier(Reading("tel0000000004", 95))
        self.engine.observe_identifier(Reading("tel0000000005", 95))
        self.assertTrue(self.engine.identifier_change_suspected)
        for _ in range(3):
            self.engine.observe_identifier(Reading("tel0000000001", 95))
        self.assertFalse(self.engine.identifier_change_suspected)
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_SCORE)
        self.capture_score("79%")
        self.assertEqual(self.engine.pending["raw_identifier"], "tel0000000001")

    def test_score_seen_during_ambiguous_identifier_change_requires_fresh_clear(self):
        self.capture_identifier()
        for _ in range(3):
            self.engine.observe_score(Reading("", 0))
        self.engine.observe_identifier(Reading("tel0000000004", 95))
        self.assertTrue(self.engine.identifier_change_suspected)
        for _ in range(4):
            self.assertFalse(self.engine.observe_score(Reading("82%", 95)))
        for _ in range(3):
            self.engine.observe_identifier(Reading("tel0000000001", 95))
        self.assertFalse(self.engine.identifier_change_suspected)
        self.assertFalse(self.engine.score_armed)
        for _ in range(3):
            self.assertFalse(self.engine.observe_score(Reading("82%", 95)))
        self.assertIsNone(self.engine.pending)
        self.capture_score("82%")
        self.assertEqual(self.engine.pending["raw_identifier"], "tel0000000001")

    def test_low_confidence_different_identifier_quarantines_score_until_reconfirmed(self):
        self.capture_identifier()
        for _ in range(3):
            self.engine.observe_identifier(Reading("tel0000000004", 40))
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_SCORE)
        self.assertEqual(self.engine.identifier, "tel0000000001")
        self.assertTrue(self.engine.identifier_change_suspected)
        self.capture_score("79%")
        self.assertIsNone(self.engine.pending)
        for _ in range(3):
            self.engine.observe_identifier(Reading("tel0000000001", 95))
        self.assertFalse(self.engine.identifier_change_suspected)
        self.capture_score("79%")
        self.assertEqual(self.engine.pending["raw_identifier"], "tel0000000001")

    def test_score_disappearing_and_reappearing_requires_stability_again(self):
        self.capture_identifier()
        for _ in range(3):
            self.engine.observe_score(Reading("", 0))
        self.engine.observe_score(Reading("83%", 95))
        self.engine.observe_score(Reading("83%", 95))
        self.assertFalse(self.engine.observe_score(Reading("", 0)))
        self.assertIsNone(self.engine.pending)
        self.assertFalse(self.engine.observe_score(Reading("83%", 95)))
        self.assertFalse(self.engine.observe_score(Reading("83%", 95)))
        self.assertTrue(self.engine.observe_score(Reading("83%", 95)))
        self.assertEqual(self.engine.pending["raw_total_score"], "83%")

    def test_repeated_score_frames_do_not_create_duplicate_capture_or_submission(self):
        self.capture_identifier()
        self.capture_score("91%")
        submission_id = self.engine.pending["submission_id"]
        self.assertFalse(self.engine.operator_reset())
        self.assertEqual(self.engine.pending["submission_id"], submission_id)
        for _ in range(10):
            self.assertFalse(self.engine.observe_score(Reading("91%", 95)))
        self.engine.submit_once()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["submission_id"], submission_id)
        self.assertEqual(self.engine.phase, Phase.RESET)

    def test_delayed_score_within_configured_timeout_is_accepted(self):
        now = [0.0]
        engine, _calls = self.make_engine(now, timeout=200)
        self.capture_identifier(engine=engine)
        now[0] = 120.0
        self.capture_score("86%", engine=engine)
        self.assertEqual(engine.pending["raw_identifier"], "tel0000000001")
        self.assertEqual(engine.pending["raw_total_score"], "86%")

    def test_expired_session_is_cleared_and_requires_fresh_id(self):
        now = [0.0]
        engine, _calls = self.make_engine(now, timeout=10)
        self.capture_identifier(engine=engine)
        now[0] = 10.0
        self.assertFalse(engine.observe_score(Reading("86%", 95)))
        self.assertEqual(engine.phase, Phase.WAITING_FOR_ID)
        self.assertIsNone(engine.identifier)
        self.assertFalse(engine.ready_for_id)
        self.assertFalse(engine.observe_score_clear(Reading("86%", 95)))
        self.assertFalse(engine.observe_identifier(Reading("tel0000000004", 95)))
        self.clear_screen(engine)
        self.capture_identifier("tel0000000004", engine=engine)
        self.capture_score("74%", engine=engine)
        self.assertEqual(engine.pending["raw_identifier"], "tel0000000004")

    def test_operator_reset_abandons_session_and_requires_score_clear(self):
        self.capture_identifier()
        self.assertTrue(self.engine.operator_reset())
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_ID)
        self.assertIsNone(self.engine.identifier)
        self.assertFalse(self.engine.ready_for_id)
        self.assertFalse(self.engine.observe_score_clear(Reading("90%", 95)))
        self.assertIsNone(self.engine.pending)

    def test_timeout_is_loaded_from_config(self):
        profile_path = self.artifact_path("timeout-profile.json")
        profile_path.write_text(json.dumps(profile_data()))
        config_path = self.artifact_path("timeout-config.json")
        config_path.write_text(json.dumps({
            "server_url": "http://localhost:8000", "event_id": EVENT_ID,
            "station_code": "SIM-01", "stable_reads": 3,
            "profile_path": profile_path.name,
            "session_timeout_seconds": 180,
        }))
        with mock.patch.dict(os.environ, {"SIMULATOR_INGESTION_TOKEN": "test-token-placeholder"}):
            loaded = HelperConfig.load(config_path)
        self.assertEqual(loaded.session_timeout_seconds, 180)

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
        self.assertEqual(parse_identifier("tel0000000001"), "tel0000000001")
        self.assertEqual(parse_identifier("000 000 0001"), "0000000001")
        self.assertEqual(parse_identifier("tel0000000000"), "tel0000000000")
        self.assertIsNone(parse_identifier("PASS0000000001"))
        for raw in ("83.33%", "83.33", "100%", "0%"):
            self.assertIsNotNone(parse_score(raw))
        for raw in ("O%", "101%", "83..3%", "PASS", "83.333%"):
            self.assertIsNone(parse_score(raw))
        self.clear_screen()
        for _ in range(5):
            self.engine.observe_identifier(Reading("tel00B0000001", 90))
        self.assertEqual(self.engine.phase, Phase.WAITING_FOR_ID)

    def test_config_and_auth_validation(self):
        profile_path = self.artifact_path("auth-profile.json")
        profile_path.write_text(json.dumps(profile_data()))
        config_path = self.artifact_path("auth-config.json")
        config_path.write_text(json.dumps({
            "server_url": "http://localhost:8000", "event_id": EVENT_ID,
            "station_code": "SIM-01", "stable_reads": 3,
            "profile_path": profile_path.name,
        }))
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                HelperConfig.load(config_path)
            self.assertEqual(HelperConfig.load(config_path, require_token=False).profile.profile_version, "7")
        with mock.patch.dict(os.environ, {"SIMULATOR_INGESTION_TOKEN": "test-token-placeholder"}):
            self.assertEqual(HelperConfig.load(config_path).token, "test-token-placeholder")
        self.assertEqual(authorization_header("test-token-placeholder"), "Bearer test-token-placeholder")
        with self.assertRaises(ConfigurationError):
            authorization_header("bad token")
        with self.assertRaises(ConfigurationError):
            Rectangle.from_dict({"x": 990, "y": 0, "width": 20, "height": 10}, 1000, 600)

    def test_rectangle_calibration_and_profile_loading(self):
        rect = rectangle_from_drag((100, 50), (200, 100), 2, 2, 1000, 600)
        self.assertEqual(rect.as_dict(), {"x": 200, "y": 100, "width": 200, "height": 100})
        with self.assertRaises(ConfigurationError):
            rectangle_from_drag((100, 100), (100, 100), 2, 2, 1000, 600)
        exported = save_region(self.artifact_path("calibrated-profile.json"), profile_data(), "identifier", rect, CONTEXT)
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
        from .windows import ScoreOCRAnalysis
        expected = ScoreOCRAnalysis(Reading("83.33", 93), "83.33", True, ())
        with mock.patch("simulator_helper.windows.analyze_score_image", return_value=expected):
            reading = read_region(FakeImage(), "score")
        self.assertEqual(reading.text, "83.33")
        self.assertEqual(reading.confidence, 93)
        with mock.patch.dict("sys.modules", {"PIL": fake_pil}), mock.patch("subprocess.run", return_value=completed) as ocr:
            read_region(FakeImage(), "identifier")
        self.assertEqual(ocr.call_args.args[0][3:], ["--psm", "8", "tsv"])

    def test_ocr_uses_configured_absolute_tesseract_without_path(self):
        from .windows import resolve_tesseract
        installed = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if not installed.is_file():
            self.skipTest(f"Installed Tesseract not found at {installed}")

        from PIL import Image
        tsv = b"level\tconf\ttext\n5\t93\t0000000003\n"
        completed = types.SimpleNamespace(returncode=0, stdout=tsv)
        import subprocess
        stripped_path_env = dict(os.environ)
        stripped_path_env["PATH"] = ""
        version = subprocess.run([str(installed), "--version"], env=stripped_path_env,
                                 capture_output=True, timeout=8, check=False)
        self.assertEqual(version.returncode, 0, version.stderr.decode("utf-8", errors="replace"))
        self.assertIn(b"tesseract v", version.stdout.lower())
        with (mock.patch.dict(os.environ, {"PATH": "", "TESSERACT_CMD": ""}),
              mock.patch("simulator_helper.windows.subprocess.run", return_value=completed) as ocr):
            resolved = resolve_tesseract(str(installed))
            self.assertEqual(resolved, str(installed))
            reading = read_region(Image.new("RGB", (200, 50), "white"), "identifier",
                                  tesseract_cmd=resolved)
        self.assertEqual(reading.text, "0000000003")
        self.assertEqual(ocr.call_args.args[0][0], str(installed))

    def test_invalid_configured_tesseract_reports_exact_path(self):
        from .windows import resolve_tesseract
        missing = str(Path(self.artifact_path("missing-tesseract.exe")).resolve())
        with self.assertRaisesRegex(FileNotFoundError, "Configured Tesseract executable not found:.*missing-tesseract.exe"):
            resolve_tesseract(missing)

    def test_tesseract_subprocess_uses_hidden_window_settings_on_windows(self):
        from . import windows
        completed = types.SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")
        with (mock.patch("simulator_helper.windows.os.name", "nt"),
              mock.patch("simulator_helper.windows.subprocess.run", return_value=completed) as run):
            result = windows._run_tesseract([r"C:\Program Files\Tesseract-OCR\tesseract.exe", "stdin"],
                                            input_data=b"image-bytes")
        self.assertIs(result, completed)
        self.assertEqual(run.call_args.kwargs["creationflags"],
                         getattr(windows.subprocess, "CREATE_NO_WINDOW", 0x08000000))
        self.assertEqual(run.call_args.kwargs["input"], b"image-bytes")
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], 8)
        self.assertFalse(run.call_args.kwargs["check"])


    def test_last_submission_status_survives_reset_and_restart(self):
        self.capture_identifier()
        self.capture_score()
        self.engine.submit_once()
        self.assertEqual(StateStore(self.config.state_path).read()["last_result"]["http_status"], 201)
        self.clear_screen()
        restarted = CaptureEngine(self.config, StateStore(self.config.state_path), self.post)
        self.assertEqual(restarted.last_result["outcome"], "matched")
        self.assertEqual(restarted.phase, Phase.WAITING_FOR_ID)


class MonitorGuiTests(unittest.TestCase):
    def setUp(self):
        from .gui import DiagnosticHistory
        self.root = Path(__file__).resolve().parent
        self.test_prefix = f".test-{uuid.uuid4().hex}-"
        self.path = self.root / (self.test_prefix + "history.json")
        self.history = DiagnosticHistory(self.path)
        profile = Profile.from_dict(profile_data())
        self.config = HelperConfig("http://localhost:8000", EVENT_ID, "SIM-01", profile,
                                   "test-token-placeholder", 0.75, 3, 70, self.artifact_path("state.json"))

    def artifact_path(self, name):
        return self.root / (self.test_prefix + name)

    def tearDown(self):
        for path in self.root.glob(self.test_prefix + "*"):
            if path.is_file():
                path.unlink()

    def test_capture_poll_keeps_headline_stable_when_phase_is_waiting_for_ticket(self):
        from . import gui
        class Variable:
            def __init__(self):
                self.value = None
                self.values = []
            def set(self, value):
                self.value = value
                self.values.append(value)

        app = object.__new__(gui.MonitorApp)
        app.events = __import__("queue").Queue()
        app.closing = True
        app.logger = logging.getLogger("test.monitor.layout")
        app.status_var = Variable()
        app.phase_var = Variable()
        app.id_var = Variable()
        app.age_var = Variable()
        app.warning_var = Variable()
        app.event_var = Variable()
        app.last_success = None
        app.last_success_monotonic = None
        app.events.put(("capture_ok", {"time": "12:00:00"}))
        app.events.put(("reading", {
            "last_ocr": "12:00:00", "phase": Phase.WAITING_FOR_ID.value,
            "identifier": "—", "session_age": None, "timeout": 300,
            "identifier_change": False,
            "calibration_progress": {"qualified": True, "matching_values": [], "required_values": 4},
            "event": "Waiting for ticket", "identifier_confidence": 0, "score_confidence": 0,
        }))
        app._poll_events()
        self.assertEqual(app.phase_var.value, "Waiting For Id")
        self.assertEqual(app.status_var.value, "●  Capture Running")
        self.assertEqual(app.status_var.values, ["●  Capture Running"],
                         "capture_ok must not overwrite the current phase line every poll")

    @staticmethod
    def _tsv_result(text, confidence=93):
        headers = ("level", "page_num", "block_num", "par_num", "line_num", "word_num",
                   "left", "top", "width", "height", "conf", "text")
        values = ("5", "1", "1", "1", "1", "1", "0", "0", "20", "10", str(confidence), text)
        tsv = ("\t".join(headers) + "\n" + "\t".join(values) + "\n").encode()
        return types.SimpleNamespace(returncode=0, stdout=tsv)

    def _analyze_sequence(self, outputs):
        from PIL import Image
        from .windows import analyze_score_image
        with mock.patch("simulator_helper.windows.subprocess.run", side_effect=[
                self._tsv_result(text, confidence) for text, confidence in outputs]):
            return analyze_score_image(Image.new("RGB", (141, 66), "white"))

    def test_score_ocr_consensus_parses_common_integer_and_decimal_scores(self):
        from .gui import compare_score_values
        for expected in ("50", "90", "37.5", "31.5", "3.75", "50%", "37.5%", "90%"):
            analysis = self._analyze_sequence([(expected, 93)] * 6)
            self.assertTrue(analysis.accepted, expected)
            normalized = expected.rstrip("%").rstrip("0").rstrip(".") if "." in expected else expected.rstrip("%")
            self.assertEqual(analysis.parsed_score, normalized)
            self.assertTrue(compare_score_values(expected, analysis))

    def test_exact_33_33_percent_votes_despite_confidence_spread(self):
        outputs = [("33.33%", 96), ("33.33 %", 88), ("33.33", 65),
                   ("33.33%", 92), ("33.33 %", 94), ("33.33%", 89)]
        analysis = self._analyze_sequence(outputs)
        self.assertTrue(analysis.accepted)
        self.assertEqual(analysis.parsed_score, "33.33")
        self.assertEqual(analysis.reading.text, "33.33")
        self.assertEqual(analysis.reading.confidence, 88)
        from .gui import compare_score_values
        self.assertTrue(compare_score_values("33.33%", analysis))

    def test_saved_33_33_ocr_formatting_noise_does_not_veto_high_confidence_variants(self):
        outputs = [("33.33%0", 0), ("33.33%", 82.099052),
                   ("33.33%0", 0), ("33.33%", 84.156693),
                   ("33.33%0", 0), ("33.33%", 87.072289)]
        analysis = self._analyze_sequence(outputs)
        self.assertTrue(analysis.accepted)
        self.assertEqual(analysis.parsed_score, "33.33")
        self.assertGreaterEqual(analysis.reading.confidence, 70)
        from .gui import compare_score_values
        self.assertTrue(compare_score_values("33.33%", analysis))

    def test_saved_75_examples_vote_from_valid_high_confidence_segmentation(self):
        outputs = [("13.00%", 0), ("75.00%", 70.383163),
                   ("19.00%", 0), ("75.00%", 73.548721),
                   ("19.00%0", 0), ("75.00%", 71.925224)]
        analysis = self._analyze_sequence(outputs)
        self.assertTrue(analysis.accepted)
        self.assertEqual(analysis.parsed_score, "75")

    def test_low_confidence_41_67_line_ocr_is_recovered_without_lowering_threshold(self):
        outputs = [
            ("41.67%", 94.691109), ("167%.", 0.0),
            ("41.67%", 60.327648), ("41.67%", 18.940651),
            ("41.67%", 88.737335), ("41.67%", 13.906975),
        ]
        analysis = self._analyze_sequence(outputs)
        self.assertTrue(analysis.accepted)
        self.assertEqual(analysis.parsed_score, "41.67")
        self.assertAlmostEqual(analysis.reading.confidence, 88.737335, places=4)

    def test_54_17_false_reads_at_2x_still_conflict_and_are_rejected(self):
        outputs = [
            ("04.17%", 77.909622), ("541%", 19.094299),
            ("94.17%", 75.094299), ("541%", 17.386642),
            ("04.17%", 65.270538), ("541%", 16.425713),
        ]
        analysis = self._analyze_sequence(outputs)
        self.assertFalse(analysis.accepted)
        self.assertIsNone(analysis.parsed_score)
        self.assertEqual(analysis.reading.confidence, 0)

    def test_saved_physical_score_crops_replay_without_new_false_acceptance(self):
        from .windows import analyze_score_image, resolve_tesseract
        examples = Path(__file__).resolve().parent / "score_ocr_calibration" / "examples"
        if not examples.is_dir():
            self.skipTest("Saved physical score examples are unavailable")
        sample_by_actual = {}
        for metadata_path in sorted(examples.glob("*.json")):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            actual = str(metadata.get("actual_test_score", "")).rstrip("0").rstrip(".")
            image_name = metadata.get("image_file")
            if actual in {"33.33", "41.67", "54.17", "58.33", "62.5", "75"} and image_name:
                sample_by_actual.setdefault(actual, examples / image_name)
        required = {"33.33", "41.67", "54.17", "58.33", "62.5", "75"}
        if not required.issubset(sample_by_actual):
            self.skipTest(f"Saved physical examples missing: {sorted(required - sample_by_actual.keys())}")
        from PIL import Image
        executable = resolve_tesseract(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        for actual, image_path in sample_by_actual.items():
            with Image.open(image_path) as image:
                result = analyze_score_image(image.copy(), tesseract_cmd=executable)
            if actual in {"54.17", "58.33"}:
                self.assertFalse(result.accepted,
                                 f"conflicting {actual} crop must remain quarantined: {result.variants}")
            else:
                self.assertTrue(result.accepted, f"saved {actual} crop should pass: {result.variants}")
                self.assertEqual(result.parsed_score, actual)

    def test_score_ocr_reports_physical_mismatches_instead_of_claiming_match(self):
        from .gui import compare_score_values
        self.assertFalse(compare_score_values("50", self._analyze_sequence([("90", 95)] * 6)))
        self.assertFalse(compare_score_values("37.5", self._analyze_sequence([("31.5", 95)] * 6)))

    def test_score_ocr_rejects_low_confidence_malformed_and_clipped_values(self):
        analysis = self._analyze_sequence([("50", 40)] * 6)
        self.assertFalse(analysis.accepted)
        self.assertEqual(analysis.reading.confidence, 0)
        for malformed in ("50.", "50..0", "101", "101%", "O0", "37,5", "33.3.3%", "37.5%%"):
            result = self._analyze_sequence([(malformed, 96)] * 6)
            self.assertFalse(result.accepted, malformed)
            self.assertEqual(result.reading.confidence, 0)

    def test_score_ocr_rejects_inconsistent_variant_families(self):
        outputs = [("90", 95), ("90", 94), ("50", 96), ("50", 95), ("37.5", 92), ("37.5", 91)]
        result = self._analyze_sequence(outputs)
        self.assertFalse(result.accepted)
        self.assertIsNone(result.parsed_score)
        self.assertGreater(len(result.reading.text), 0, "ambiguous nonblank OCR must not look like a blank score screen")

    def test_score_capture_stays_blocked_until_four_distinct_physical_matches(self):
        from .windows import SCORE_OCR_PIPELINE_VERSION, score_calibration_progress
        progress = score_calibration_progress(self.root)
        self.assertFalse(progress["qualified"])
        legacy = {"pipeline_version": "consensus-v1", "actual_test_score": "88",
                  "matches_actual": True, "captured_at": "2026-10-09T12:00:00+00:00"}
        self.artifact_path("legacy-cal.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertFalse(score_calibration_progress(self.root)["qualified"],
                         "examples from the superseded OCR pipeline cannot qualify the current pipeline")
        for index, score in enumerate(("50", "90", "37.5", "31.5")):
            item = {"pipeline_version": SCORE_OCR_PIPELINE_VERSION, "actual_test_score": score,
                    "matches_actual": True, "captured_at": f"2026-10-09T10:00:0{index}+00:00"}
            path = self.artifact_path(f"cal-{index}.json")
            path.write_text(json.dumps(item), encoding="utf-8")
        self.assertTrue(score_calibration_progress(self.root)["qualified"])
        failure = {"pipeline_version": SCORE_OCR_PIPELINE_VERSION, "actual_test_score": "50",
                   "matches_actual": False, "captured_at": "2026-10-09T11:00:00+00:00"}
        self.artifact_path("cal-failure.json").write_text(json.dumps(failure), encoding="utf-8")
        self.assertFalse(score_calibration_progress(self.root)["qualified"])

    def test_unqualified_score_cannot_enter_capture_engine_pending_state(self):
        from .windows import gate_uncalibrated_score
        engine = CaptureEngine(self.config, StateStore(self.artifact_path("gated-state.json")), lambda _payload: (201, {}))
        for _ in range(3):
            engine.observe_score_clear(Reading("", 0))
        for _ in range(3):
            engine.observe_identifier(Reading("tel0000000001", 95))
        for _ in range(3):
            engine.observe_score(Reading("", 0))
        for _ in range(6):
            engine.observe_score(gate_uncalibrated_score(Reading("50", 99), False))
        self.assertEqual(engine.phase, Phase.WAITING_FOR_SCORE)
        self.assertIsNone(engine.pending)

    def test_diagnostic_history_persists_masked_data_and_deduplicates(self):
        from .gui import DiagnosticHistory
        row = {"capture_id": "capture-1", "time": "2026-10-09 12:00:00",
               "identifier": "***34", "score": "83.3", "status": "Captured Locally",
               "delivery": "Diagnostic only — not submitted", "raw_identifier": "synthetic-short-id",
               "token": "test-token-must-not-persist"}
        self.assertTrue(self.history.append(row))
        self.assertFalse(self.history.append(row))
        loaded = DiagnosticHistory(self.path)
        self.assertEqual(len(loaded.rows), 1)
        self.assertEqual(loaded.rows[0]["identifier"], "***34")
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("synthetic-short-id", text)
        self.assertNotIn("test-token-must-not-persist", text)

    def test_worker_integration_records_score_without_touching_durable_state(self):
        from . import gui
        config = replace(self.config, poll_interval=0.2)
        readings = []
        readings.extend([Reading("", 0), Reading("", 0)] * 3)  # startup clear
        readings.extend([Reading("tel0000000001", 95), Reading("", 0)] * 3)
        readings.extend([Reading("tel0000000001", 95), Reading("", 0)] * 3)  # score clear
        readings.extend([Reading("tel0000000001", 95), Reading("83.33%", 95)] * 3)
        reading_iter = iter(readings)
        class FakeCapture:
            def verify(self, _profile): pass
            def grab(self, _rect): return object()
            def close(self): pass
        events = __import__("queue").Queue()
        durable = self.artifact_path("durable-state.json")
        config = replace(config, state_path=durable)
        worker = gui.MonitorWorker(self.path, events, capture_factory=FakeCapture)
        with mock.patch.object(gui.HelperConfig, "load", return_value=config), \
             mock.patch.object(gui, "read_region", side_effect=lambda *_args, **_kwargs: next(reading_iter)), \
             mock.patch.object(gui, "score_calibration_progress", return_value={
                 "required_values": 4, "matching_values": ["50", "90", "37.5", "31.5"],
                 "mismatched_values": [], "qualified": True}):
            worker.start()
            captured = None
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline and captured is None:
                    try:
                        kind, data = events.get(timeout=0.5)
                    except __import__("queue").Empty:
                        continue
                    if kind == "captured":
                        captured = data["row"]
                        worker.stop()
            finally:
                worker.stop()
                worker.join(timeout=3)
        self.assertIsNotNone(captured, "diagnostic capture should reach the local history event")
        self.assertEqual(captured["identifier"], "***01")
        self.assertEqual(captured["score"], "83.33%")
        self.assertEqual(captured["delivery"], "Diagnostic only — not submitted")
        self.assertFalse(durable.exists(), "diagnostic worker must not read/write the durable submission state")
        self.assertFalse(worker.is_alive(), "worker must stop cleanly")

    def test_worker_reports_configuration_error_without_starting_capture(self):
        from . import gui
        events = __import__("queue").Queue()
        worker = gui.MonitorWorker(self.path, events)
        with mock.patch.object(gui.HelperConfig, "load", side_effect=ConfigurationError("bad local config")):
            worker.start()
            worker.join(timeout=2)
        messages = []
        while not events.empty():
            messages.append(events.get_nowait())
        self.assertIn("fatal", [kind for kind, _ in messages])
        self.assertIn("stopped", [kind for kind, _ in messages])
        self.assertFalse(worker.is_alive())


def _scorecard_xml(login_code="tel:0000123456", *, failure="FALSE", filename_extra=""):
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ScoreCard><Header><Failure>{failure}</Failure><FailureReason></FailureReason>
<FailureCode>0</FailureCode><LapsCompleted>0</LapsCompleted><LoginCode>{login_code}</LoginCode>
<FutureHeaderField>retained</FutureHeaderField></Header><Detail><Field>
<Code>TIME</Code><Label>Execution Time</Label><ImperialValue>45</ImperialValue>
<ImperialUnits>seconds</ImperialUnits><MetricValue>45</MetricValue>
<MetricUnits>seconds</MetricUnits><DataType>Number</DataType><ExerciseSpecific>kept</ExerciseSpecific>
</Field></Detail><FutureRootField>{filename_extra}</FutureRootField></ScoreCard>'''.encode("utf-8")


class XMLResultCollectionTests(unittest.TestCase):
    SAMPLE_DIR = Path(__file__).resolve().parent / "no_live_simulator_samples"
    TEST_ROOT = Path(__file__).resolve().parent / ".xml-test-workspace"

    def setUp(self):
        self.TEST_ROOT.mkdir(exist_ok=True)
        self.root = self.TEST_ROOT / f"run-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.source = self.root / "source"
        self.archive = self.root / "archive"
        self.db_path = self.archive / "results.sqlite3"
        self.source.mkdir()
        self.repo = XMLResultRepository(self.db_path, self.archive)

    def tearDown(self):
        for path in sorted(self.root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        self.root.rmdir()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.TEST_ROOT.rmdir()
        except OSError:
            pass

    def build_gui(self, *, autostart=False, preferences_path=None):
        import tkinter as tk
        from . import gui
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk desktop display is unavailable in this test session: {exc}")
        root.withdraw()
        archive = self.root / "gui-archive"
        config_path = self.root / "gui-config.json"
        config_path.write_text(json.dumps({
            "xml_source_directory": str(self.source),
            "xml_archive_directory": str(archive),
            "xml_database_path": str(archive / "results.sqlite3"),
            "xml_poll_interval": 1.0,
        }), encoding="utf-8")
        try:
            app = gui.XMLMonitorApp(root, config_path=config_path, autostart=autostart,
                                    preferences_path=preferences_path or self.root / "ui-preferences.json")
        except Exception:
            root.destroy()
            raise
        return root, app

    def add_gui_result(self, app, number: int, captured_at: str):
        path = self.source / f"EXC - Truck Loading - UI test {number}.xml"
        raw_xml = _scorecard_xml(f"tel:{number:010d}")
        path.write_bytes(raw_xml)
        record = app.repository.import_bytes(FileSignature.from_path(path), raw_xml)["record"]
        with app.repository._connect() as db:
            db.execute("UPDATE results SET imported_at=? WHERE result_id=?", (captured_at, record["result_id"]))
        return record

    def test_parse_ticket_exercise_unknown_fields_and_failure_without_scoring(self):
        parsed = parse_scorecard(_scorecard_xml(), "AEXC - Dig Footings - sample.xml")
        self.assertEqual(parsed.ticket, "0000123456")
        self.assertEqual(parsed.ticket_status, "valid")
        self.assertEqual(parsed.exercise, "Dig Footings")
        self.assertEqual(parsed.failure_status, "No failure reported; completion unknown")
        self.assertEqual(parsed.validation_status, "parsed")
        self.assertEqual(parsed.header["FutureHeaderField"], "retained")
        self.assertIn("FutureRootField", parsed.header["_UnknownRootElementsXML"])
        self.assertEqual(parsed.measurements[0]["ExerciseSpecific"], "kept")
        self.assertIsNone(getattr(parsed, "overall_score", None))

    def test_exact_ticket_validation_and_uncertain_exercise_flag(self):
        invalid = parse_scorecard(_scorecard_xml("tel:123456789"), "unknown exercise.xml")
        self.assertIsNone(invalid.ticket)
        self.assertEqual(invalid.ticket_status, "invalid")
        self.assertEqual(invalid.exercise_status, "uncertain")
        self.assertEqual(invalid.validation_status, "needs_review")
        self.assertEqual(identify_exercise("EXC - Truck Loading - sample.xml"),
                         ("Truck Loading", "identified_from_filename"))

    def test_xml_dtd_and_entity_declarations_are_rejected(self):
        payload = b'<!DOCTYPE ScoreCard [<!ENTITY x "bad">]><ScoreCard />'
        with self.assertRaisesRegex(Exception, "DOCTYPE"):
            parse_scorecard(payload, "Truck Loading.xml")

    def test_scanner_waits_for_stable_file_then_archives_exact_bytes_once(self):
        path = self.source / "AEXC - Dig Footings - run.xml"
        payload = _scorecard_xml()
        path.write_bytes(payload)
        scanner = XMLDirectoryScanner(self.source, self.repo, incomplete_grace_seconds=0)
        self.assertEqual(scanner.scan_once()["imported"], [])
        result = scanner.scan_once()
        self.assertEqual(len(result["imported"]), 1)
        record = result["imported"][0]
        archive_path = Path(record["archive_path"])
        self.assertEqual(archive_path.read_bytes(), payload)
        self.assertEqual(record["ticket"], "0000123456")
        self.assertEqual(record["archive_status"], "archived")
        self.assertEqual(scanner.scan_once()["imported"], [])
        self.assertEqual(self.repo.counts(), {"total": 1, "archived": 1, "review": 0})

    def test_retries_temporarily_locked_file_without_losing_it(self):
        path = self.source / "EXC - Truck Loading - run.xml"
        path.write_bytes(_scorecard_xml())
        attempts = {"count": 0}

        def read(path):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise PermissionError("temporarily locked")
            return path.read_bytes()

        scanner = XMLDirectoryScanner(self.source, self.repo, incomplete_grace_seconds=0, read_bytes=read)
        scanner.scan_once()
        first = scanner.scan_once()
        self.assertTrue(first["errors"])
        self.assertEqual(self.repo.counts()["total"], 0)
        self.assertEqual(len(scanner.scan_once()["imported"]), 1)

    def test_malformed_xml_is_archived_as_review_and_does_not_block_later_files(self):
        (self.source / "unknown exercise.xml").write_bytes(b"<ScoreCard><Header>")
        (self.source / "EXC - Truck Loading - valid.xml").write_bytes(_scorecard_xml())
        scanner = XMLDirectoryScanner(self.source, self.repo, incomplete_grace_seconds=0)
        scanner.scan_once()
        result = scanner.scan_once()
        self.assertEqual(len(result["imported"]), 2)
        records = self.repo.recent()
        self.assertEqual(self.repo.counts()["review"], 1)
        self.assertTrue(any(row["validation_status"] == "invalid_xml" and row["archive_status"] == "archived" for row in records))
        self.assertTrue(any(row["exercise"] == "Truck Loading" and row["validation_status"] == "parsed" for row in records))

    def test_incomplete_file_that_finishes_is_retried_and_parsed(self):
        path = self.source / "EXC - Truck Loading - delayed.xml"
        path.write_bytes(b"<ScoreCard><Header>")
        scanner = XMLDirectoryScanner(self.source, self.repo, incomplete_grace_seconds=60)
        scanner.scan_once()
        scanner.scan_once()
        self.assertEqual(self.repo.counts()["total"], 0)
        path.write_bytes(_scorecard_xml())
        scanner.scan_once()
        result = scanner.scan_once()
        self.assertEqual(len(result["imported"]), 1)
        self.assertEqual(result["imported"][0]["validation_status"], "parsed")

    def test_identical_content_from_distinct_attempts_is_preserved(self):
        payload = _scorecard_xml()
        first_path = self.source / "EXC - Truck Loading - attempt-a.xml"
        second_path = self.source / "EXC - Truck Loading - attempt-b.xml"
        first_path.write_bytes(payload)
        scanner = XMLDirectoryScanner(self.source, self.repo, incomplete_grace_seconds=0)
        scanner.scan_once()
        scanner.scan_once()
        second_path.write_bytes(payload)
        scanner.scan_once()
        result = scanner.scan_once()
        self.assertEqual(len(result["imported"]), 1)
        records = self.repo.recent()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["sha256"], records[1]["sha256"])
        self.assertNotEqual(records[0]["result_id"], records[1]["result_id"])

    def test_database_and_archive_survive_repository_restart_and_source_deletion(self):
        path = self.source / "AEXC - Dig Footings - restart.xml"
        payload = _scorecard_xml(failure="TRUE")
        path.write_bytes(payload)
        scanner = XMLDirectoryScanner(self.source, self.repo, incomplete_grace_seconds=0)
        scanner.scan_once()
        record = scanner.scan_once()["imported"][0]
        path.unlink()
        restarted = XMLResultRepository(self.db_path, self.archive)
        self.assertEqual(restarted.counts()["archived"], 1)
        self.assertEqual(Path(record["archive_path"]).read_bytes(), payload)
        self.assertEqual(restarted.recent()[0]["failure_status"], "Failed")
        self.assertIsNotNone(restarted.last_successful_scan())

    def test_pending_archive_recovers_after_interrupted_archive_write(self):
        path = self.source / "EXC - Truck Loading - recover.xml"
        payload = _scorecard_xml()
        path.write_bytes(payload)
        signature = FileSignature.from_path(path)
        with mock.patch.object(self.repo, "_finish_archive", side_effect=OSError("simulated interrupted write")):
            with self.assertRaises(OSError):
                self.repo.import_bytes(signature, payload)
        recovered = XMLResultRepository(self.db_path, self.archive)
        self.assertEqual(recovered.counts()["archived"], 1)
        self.assertEqual(Path(recovered.recent()[0]["archive_path"]).read_bytes(), payload)

    def test_missing_source_directory_is_reported_and_retried(self):
        missing = XMLDirectoryScanner(self.root / "not-present", self.repo)
        result = missing.scan_once()
        self.assertFalse(result["directory_exists"])
        self.assertTrue(result["error"])

    def test_monitor_worker_starts_scans_and_stops_cleanly(self):
        import queue
        events = queue.Queue()
        worker = XMLMonitorWorker(self.root / "not-present", self.repo, events, poll_interval=0.25)
        worker.start()
        deadline = time.monotonic() + 3
        observed = []
        while time.monotonic() < deadline and not any(kind == "xml_scan" for kind, _ in observed):
            try:
                observed.append(events.get(timeout=0.1))
            except queue.Empty:
                pass
        worker.stop()
        worker.join(timeout=3)
        while not events.empty():
            observed.append(events.get_nowait())
        self.assertFalse(worker.is_alive())
        self.assertIn("xml_monitor_started", [kind for kind, _ in observed])
        self.assertIn("xml_scan", [kind for kind, _ in observed])
        self.assertIn("xml_monitor_stopped", [kind for kind, _ in observed])

    def test_gui_status_text_reports_scan_errors_and_never_claims_submission(self):
        from . import gui
        missing = gui._xml_scan_status_message({"directory_exists": False, "error": "XML source unavailable"})
        retry = gui._xml_scan_status_message({"directory_exists": True, "errors": ["locked.xml: retry"]})
        result = gui._xml_result_status_message({"source_filename": "run.xml", "exercise": "Truck Loading",
                                                 "validation_status": "parsed"})
        self.assertIn("retry automatically", missing)
        self.assertIn("locked.xml", retry)
        self.assertIn("Pending Formula", result)
        self.assertIn("XML detected and archived", result)
        self.assertNotIn("submitted", result.casefold())

    def test_main_window_initializes_real_widget_tree_without_reading_source(self):
        root, app = self.build_gui()
        try:
            root.update_idletasks()
            self.assertTrue(app.results_tree.winfo_exists())
            self.assertTrue(app.status_indicator.winfo_exists())
            self.assertEqual(app.monitor_state, "Stopped")
            self.assertEqual(tuple(app.results_tree["columns"]), ("time", "ticket", "exercise", "score"))
            self.assertEqual(app.root.title(), "BTW Simulator Results")
            self.assertEqual(tuple(app.result_count_combo["values"]), ("10", "25", "50", "100"))
            self.assertEqual(app.result_count, 10)
            self.assertFalse(any(self.source.iterdir()), "construction must not scan the source directory")
        finally:
            root.destroy()

    def test_main_recent_results_shows_newest_ten_masked_and_pending_formula(self):
        root, app = self.build_gui()
        try:
            records = [self.add_gui_result(app, n, f"2026-01-01T00:00:{n:02d}+00:00") for n in range(12)]
            app._refresh_results()
            items = app.results_tree.get_children()
            self.assertEqual(len(items), 10)
            self.assertEqual(items[0], records[-1]["result_id"])
            self.assertEqual(items[-1], records[-10]["result_id"])
            values = app.results_tree.item(items[0], "values")
            self.assertEqual(values[1], "••••••0011")
            self.assertEqual(values[3], "Pending Formula")
            self.assertNotIn("0000000011", " ".join(values))
            self.assertEqual(app.results_tree.item(items[0], "tags"), ("stripe_even",))
            self.assertEqual(app.results_tree.item(items[1], "tags"), ("stripe_odd",))
        finally:
            root.destroy()

    def test_new_result_event_refreshes_main_and_settings_tables(self):
        root, app = self.build_gui()
        try:
            first = self.add_gui_result(app, 1, "2026-01-01T00:00:01+00:00")
            app._refresh_results()
            settings = app.open_settings()
            self.assertEqual(len(app.results_tree.get_children()), 1)
            second = self.add_gui_result(app, 2, "2026-01-01T00:00:02+00:00")
            app._handle_event("xml_result", {"record": second})
            self.assertEqual(len(app.results_tree.get_children()), 2)
            self.assertEqual(app.results_tree.get_children()[0], second["result_id"])
            self.assertEqual(len(settings.results_tree.get_children()), 2)
            self.assertNotEqual(first["result_id"], second["result_id"])
        finally:
            root.destroy()

    def test_recent_result_count_selection_refreshes_and_persists_across_restart(self):
        from . import gui
        preference_path = self.root / "ui-preferences.json"
        root, app = self.build_gui(preferences_path=preference_path)
        try:
            for n in range(30):
                self.add_gui_result(app, n, f"2026-01-01T00:00:{n:02d}+00:00")
            app.result_count_var.set("25")
            app._on_result_count_changed()
            self.assertEqual(app.result_count, 25)
            self.assertEqual(len(app.results_tree.get_children()), 25)
            self.assertEqual(app.result_count_summary.cget("text"), "Showing 25 newest")
            self.assertEqual(json.loads(preference_path.read_text(encoding="utf-8"))["result_count"], "25")
        finally:
            root.destroy()
        root2, restarted = self.build_gui(preferences_path=preference_path)
        try:
            self.assertEqual(restarted.result_count, 25)
            self.assertEqual(restarted.result_count_var.get(), "25")
        finally:
            root2.destroy()

    def test_result_time_uses_clock_today_and_date_for_older_results(self):
        from datetime import datetime, timezone
        from .gui import _display_result_time
        now = datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc).astimezone()
        today = _display_result_time("2026-10-09T09:07:00+00:00", now=now)
        older = _display_result_time("2026-10-08T09:07:00+00:00", now=now)
        self.assertIn("AM", today)
        self.assertNotIn("2026", today)
        self.assertIn("Oct 08, 2026", older)
        self.assertIn("AM", older)

    def test_main_theme_uses_requested_palette_without_red_result_text(self):
        root, app = self.build_gui()
        try:
            style = __import__("tkinter.ttk", fromlist=["Style"]).Style(root)
            self.assertEqual(root.cget("bg"), "#E5E5E5")
            self.assertEqual(style.lookup("BTW.Treeview.Heading", "background"), "#333333")
            self.assertEqual(style.lookup("BTW.Treeview.Heading", "foreground"), "#ffffff")
            self.assertIn("#FFFFFF", str(app.results_tree.tag_configure("stripe_even")["background"]))
            self.assertIn("#F2F2F2", str(app.results_tree.tag_configure("stripe_odd")["background"]))
        finally:
            root.destroy()

    def test_monitoring_status_tracks_worker_and_source_health(self):
        from . import gui
        root, app = self.build_gui()

        class FakeWorker:
            running = False
            def start(self): self.running = True
            def is_alive(self): return self.running
            def stop(self): self.running = False

        fake = FakeWorker()
        app.worker_factory = lambda *_args, **_kwargs: fake
        try:
            app.start()
            self.assertEqual(app.monitor_state, "Starting")
            self.assertNotEqual(app.monitor_state, "Active", "GUI must wait for a successful source scan")
            app._handle_event("xml_scan", {"directory_exists": True, "errors": [], "scanned_at": "2026-01-01T00:00:03+00:00"})
            self.assertEqual(app.monitor_state, "Active")
            app._handle_event("xml_scan", {"directory_exists": True, "errors": ["locked file retry"]})
            self.assertEqual(app.monitor_state, "Attention")
            app._handle_event("xml_scan", {"directory_exists": False, "error": "source unavailable"})
            self.assertEqual(app.monitor_state, "Unavailable")
            with mock.patch.object(app.logger, "error"):
                app._handle_event("xml_monitor_error", {"error": "worker failed"})
            self.assertEqual(app.monitor_state, "Error")
            app._handle_event("xml_monitor_stopped", {})
            self.assertEqual(app.monitor_state, "Error", "worker failure must not be overwritten by stopped event")
            self.assertTrue(app.recent_notices)
            fake.running = False
            app._set_monitor_state("Stopping")
            app._handle_event("xml_monitor_stopped", {})
            self.assertEqual(app.monitor_state, "Stopped")
            fake.running = False
            app._set_monitor_state("Stopping")
            app._handle_event("xml_monitor_stopped", {})
            self.assertEqual(app.monitor_state, "Stopped")
        finally:
            root.destroy()

    def test_settings_panel_exposes_paths_controls_health_and_legacy_access(self):
        root, app = self.build_gui()
        try:
            settings = app.open_settings()
            root.update_idletasks()
            self.assertTrue(settings.window.winfo_exists())
            self.assertIn(str(self.source), settings.source_text.get())
            self.assertIn(str(app.archive_directory), settings.archive_text.get())
            self.assertTrue(settings.start_button.winfo_exists())
            self.assertTrue(settings.stop_button.winfo_exists())
            self.assertTrue(settings.results_tree.winfo_exists())
            self.assertTrue(settings.notice_tree.winfo_exists())
        finally:
            root.destroy()

    def test_settings_result_detail_view_shows_full_ticket_and_measurements(self):
        root, app = self.build_gui()
        try:
            record = self.add_gui_result(app, 7, "2026-01-01T00:00:07+00:00")
            settings = app.open_settings()
            settings.results_tree.selection_set(record["result_id"])
            dialog = settings.view_selected_result()
            root.update_idletasks()
            self.assertEqual(dialog.record["ticket"], "0000000007")
            self.assertEqual(len(dialog.measurement_tree.get_children()), 1)
            self.assertTrue(dialog.window.winfo_exists())
        finally:
            root.destroy()

    def test_main_window_geometry_stays_fixed_during_status_and_result_updates(self):
        root, app = self.build_gui()
        try:
            root.update_idletasks()
            initial_geometry = root.geometry()
            initial_size = (root.winfo_width(), root.winfo_height())
            app._set_monitor_state("Active")
            app._set_monitor_state("Attention")
            for n in range(4):
                self.add_gui_result(app, n, f"2026-01-01T00:00:{n:02d}+00:00")
            app._refresh_results()
            root.update_idletasks()
            self.assertEqual(root.geometry(), initial_geometry)
            self.assertEqual((root.winfo_width(), root.winfo_height()), initial_size)
        finally:
            root.destroy()

    def test_real_dig_footings_and_truck_loading_samples_parse_when_available(self):
        if not self.SAMPLE_DIR.is_dir():
            self.skipTest("simulator XML samples are not present on this PC")
        files = list(self.SAMPLE_DIR.glob("*.xml"))
        self.assertTrue(files)
        exercises = set()
        for path in files:
            parsed = parse_scorecard(path.read_bytes(), path.name)
            if parsed.validation_status == "parsed":
                exercises.add(parsed.exercise)
        self.assertIn("Dig Footings", exercises)
        self.assertIn("Truck Loading", exercises)


if __name__ == "__main__":
    unittest.main()
