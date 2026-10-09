"""Pure capture contract and state machine. No Windows or Django imports."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Rectangle:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_dict(cls, data: dict, display_width: int, display_height: int) -> "Rectangle":
        if not isinstance(data, dict) or set(data) != {"x", "y", "width", "height"}:
            raise ConfigurationError("A region needs x, y, width, and height")
        if any(type(data[key]) is not int for key in data):
            raise ConfigurationError("Region coordinates must be integers")
        rect = cls(**data)
        if (rect.x < 0 or rect.y < 0 or rect.width <= 0 or rect.height <= 0
                or rect.x + rect.width > display_width or rect.y + rect.height > display_height):
            raise ConfigurationError("Region must fit within the calibrated display")
        return rect

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class Profile:
    profile_id: str
    profile_version: str
    expected_width: int
    expected_height: int
    identifier_region: Rectangle
    score_region: Rectangle
    state_regions: tuple[Rectangle, ...]
    display_context: dict
    known_identifier_prefixes: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        try:
            profile_id = str(uuid.UUID(data["profile_id"]))
            version = str(data["profile_version"]).strip()
            width = data["expected_width"]
            height = data["expected_height"]
            if not version or type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
                raise ConfigurationError("Profile version and display dimensions are required")
            context = data["display_context"]
            if not isinstance(context, dict) or context.get("width") != width or context.get("height") != height:
                raise ConfigurationError("Display context must match calibrated dimensions")
            if type(context.get("left")) is not int or type(context.get("top")) is not int:
                raise ConfigurationError("Display origin must be calibrated")
            prefixes = data.get("known_identifier_prefixes", ["tel"])
            if not isinstance(prefixes, list) or any(not isinstance(p, str) or not p.isalpha() for p in prefixes):
                raise ConfigurationError("Identifier prefixes must be alphabetic strings")
            return cls(profile_id, version, width, height,
                       Rectangle.from_dict(data["identifier_region"], width, height),
                       Rectangle.from_dict(data["score_region"], width, height),
                       tuple(Rectangle.from_dict(r, width, height) for r in data.get("state_regions", [])),
                       context, tuple(p.lower() for p in prefixes))
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ConfigurationError):
                raise
            raise ConfigurationError("Incomplete or invalid capture profile") from exc


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot read JSON configuration: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("Configuration must be a JSON object")
    return value


@dataclass(frozen=True)
class HelperConfig:
    server_url: str
    event_id: str
    station_code: str
    profile: Profile
    token: str
    poll_interval: float
    stable_reads: int
    min_confidence: float
    state_path: Path
    identifier_min_confidence: float = 45.0
    session_timeout_seconds: float = 300.0
    tesseract_cmd: str | None = None

    @classmethod
    def load(cls, path: Path, *, require_token: bool = True) -> "HelperConfig":
        data = load_json(path)
        profile_path = (path.parent / data.get("profile_path", "profile.json")).resolve()
        profile = Profile.from_dict(load_json(profile_path))
        token = os.environ.get("SIMULATOR_INGESTION_TOKEN", "")
        try:
            server_url = data["server_url"].rstrip("/")
            event_id = str(uuid.UUID(data["event_id"]))
            station_code = data["station_code"].strip()
            interval = float(data.get("poll_interval", 0.75))
            reads = data.get("stable_reads", 3)
            confidence = float(data.get("min_confidence", 70))
            identifier_confidence = float(data.get("identifier_min_confidence", 45))
            session_timeout = float(data.get("session_timeout_seconds", 300))
            tesseract_cmd = data.get("tesseract_cmd") or None
            if tesseract_cmd is not None and (not isinstance(tesseract_cmd, str) or not Path(tesseract_cmd).is_absolute()):
                raise ConfigurationError("tesseract_cmd must be an absolute executable path")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ConfigurationError("Invalid helper configuration") from exc
        if not server_url.startswith(("http://", "https://")) or not station_code or "/" in station_code:
            raise ConfigurationError("A server URL and Station code are required")
        if (type(reads) is not int or reads < 2 or not 0.2 <= interval <= 10
                or not 0 <= confidence <= 100 or not 0 < identifier_confidence <= 100
                or not math.isfinite(session_timeout) or session_timeout <= 0):
            raise ConfigurationError("Invalid polling, stability, or confidence settings")
        if require_token and not token:
            raise ConfigurationError("SIMULATOR_INGESTION_TOKEN is required for normal capture")
        state_path = (path.parent / data.get("state_path", "state.json")).resolve()
        return cls(server_url, event_id, station_code, profile, token, interval, reads,
                   confidence, state_path, identifier_confidence, session_timeout, tesseract_cmd)


def parse_identifier(text: str, prefixes: tuple[str, ...] = ("tel",)) -> str | None:
    compact = "".join(ch for ch in text if ch not in "().-" and not ch.isspace()).lower()
    if not re.fullmatch(r"[a-z]{0,12}[0-9]{10,11}", compact):
        return None
    prefix = re.match(r"[a-z]*", compact).group()
    if prefix and prefix not in prefixes:
        return None
    return compact


def parse_score(text: str) -> str | None:
    compact = "".join(text.split())
    if not re.fullmatch(r"(?:100(?:[.]0{1,2})?|[0-9]{1,2}(?:[.][0-9]{1,2})?)%?", compact):
        return None
    try:
        number = Decimal(compact.rstrip("%"))
    except InvalidOperation:
        return None
    if not number.is_finite() or not 0 <= number <= 100:
        return None
    return format(number.normalize(), "f")


@dataclass(frozen=True)
class Reading:
    text: str
    confidence: float


class StableReading:
    def __init__(self, required: int, min_confidence: float, parser):
        self.required = required
        self.min_confidence = min_confidence
        self.parser = parser
        self.key = None
        self.count = 0
        self.raw = ""

    def add(self, reading: Reading) -> str | None:
        key = self.parser(reading.text) if reading.confidence >= self.min_confidence else None
        if key is None:
            self.key, self.count, self.raw = None, 0, ""
            return None
        self.count = self.count + 1 if key == self.key else 1
        self.key, self.raw = key, reading.text.strip()
        return self.raw if self.count >= self.required else None

    def reset(self):
        self.key, self.count, self.raw = None, 0, ""


class Phase(str, Enum):
    WAITING_FOR_ID = "WAITING_FOR_ID"
    ID_CAPTURED = "ID_CAPTURED"
    WAITING_FOR_SCORE = "WAITING_FOR_SCORE"
    SCORE_CAPTURED = "SCORE_CAPTURED"
    SUBMITTING = "SUBMITTING"
    RESET = "RESET"


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        return load_json(self.path)

    def write(self, data: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".simulator-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def authorization_header(token: str) -> str:
    if not token or any(ch.isspace() for ch in token):
        raise ConfigurationError("Invalid ingestion token")
    return "Bearer " + token


class ApiClient:
    def __init__(self, config: HelperConfig):
        self.config = config

    def post(self, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(self.config.server_url + "/api/simulator/captures/", data=body,
                          headers={"Content-Type": "application/json", "Authorization": authorization_header(self.config.token)},
                          method="POST")
        try:
            with urlopen(request, timeout=8) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                error = json.loads(exc.read(4096))
            except ValueError:
                error = {"error": "http_error"}
            return exc.code, error
        except (URLError, TimeoutError, OSError):
            return 0, {"error": "network_unavailable"}


class CaptureEngine:
    def __init__(self, config: HelperConfig, store: StateStore, post, *, clock=time.monotonic):
        self.config, self.store, self.post = config, store, post
        self._clock = clock
        saved = store.read()
        if saved.get("phase") == Phase.SUBMITTING.value and isinstance(saved.get("pending"), dict):
            self.phase = Phase.SUBMITTING
            self.pending = saved["pending"]
        elif saved.get("phase") == Phase.RESET.value:
            self.phase, self.pending = Phase.RESET, None
        elif saved and set(saved) != {"last_result"}:
            raise ConfigurationError("Invalid local delivery state; preserve it for review")
        else:
            self.phase, self.pending = Phase.WAITING_FOR_ID, None
        self.identifier = None
        self.ready_for_id = False
        self.score_armed = False
        self.clear_count = 0
        self.id_filter = StableReading(config.stable_reads, config.identifier_min_confidence,
                                       lambda text: parse_identifier(text, config.profile.known_identifier_prefixes))
        self.identifier_change_filter = StableReading(
            config.stable_reads, config.identifier_min_confidence,
            lambda text: parse_identifier(text, config.profile.known_identifier_prefixes))
        self.score_filter = StableReading(config.stable_reads, config.min_confidence, parse_score)
        self.last_result = saved.get("last_result")
        self.session_started_at = None
        self.identifier_change_suspected = False
        self.last_event = "Waiting for a clear score region before accepting an identifier"

    @staticmethod
    def _same_identifier(left: str | None, right: str | None) -> bool:
        if left is None or right is None:
            return left == right
        left_digits = "".join(character for character in left if character.isdigit())
        right_digits = "".join(character for character in right if character.isdigit())
        return left_digits == right_digits if left_digits and right_digits else left == right

    @staticmethod
    def _masked_identifier(value: str | None) -> str:
        if not value:
            return "unknown"
        digits = "".join(character for character in value if character.isdigit())
        return "***" + digits[-2:] if digits else "unknown"

    def _abandon_session(self, reason: str) -> bool:
        if self.phase != Phase.WAITING_FOR_SCORE:
            return False
        previous = self._masked_identifier(self.identifier)
        self.identifier = None
        self.session_started_at = None
        self.phase = Phase.WAITING_FOR_ID
        self.ready_for_id = False
        self.score_armed = False
        self.clear_count = 0
        self.id_filter.reset()
        self.identifier_change_filter.reset()
        self.score_filter.reset()
        self.identifier_change_suspected = False
        self.last_event = f"Session {previous} abandoned: {reason}; waiting for score clear and a new identifier"
        return True

    def operator_reset(self) -> bool:
        """Abandon an unscored local session; a fresh score clear is required afterward."""
        if self.phase == Phase.WAITING_FOR_SCORE:
            return self._abandon_session("operator reset")
        if self.phase == Phase.WAITING_FOR_ID:
            self.ready_for_id = False
            self.clear_count = 0
            self.id_filter.reset()
            self.identifier_change_filter.reset()
            self.last_event = "Session reset by operator; waiting for a fresh score clear"
            return True
        return False

    def _expire_session_if_needed(self) -> bool:
        if (self.phase == Phase.WAITING_FOR_SCORE and self.session_started_at is not None
                and self._clock() - self.session_started_at >= self.config.session_timeout_seconds):
            return self._abandon_session(
                f"session timeout after {self.config.session_timeout_seconds:g} seconds")
        return False

    def observe_score_clear(self, reading: Reading) -> bool:
        """Startup and reset interlock: never pair a visible old score with a new ID."""
        if self.phase not in (Phase.WAITING_FOR_ID, Phase.RESET):
            return False
        self.clear_count = self.clear_count + 1 if not reading.text.strip() else 0
        if self.clear_count < self.config.stable_reads:
            if reading.text.strip():
                self.last_event = "Score region is not clear; identifier acceptance is quarantined"
            return False
        if self.phase == Phase.RESET:
            self.store.write({"last_result": self.last_result} if self.last_result else {})
            self.phase = Phase.WAITING_FOR_ID
            self.id_filter.reset()
        self.ready_for_id = True
        self.last_event = "Score region clear; ready to accept a stable identifier"
        return True

    def observe_identifier(self, reading: Reading) -> bool:
        if self.phase == Phase.WAITING_FOR_SCORE:
            if self._expire_session_if_needed():
                return False
            active = self.identifier
            possible = parse_identifier(reading.text, self.config.profile.known_identifier_prefixes)
            if possible is not None and not self._same_identifier(possible, active):
                if not self.identifier_change_suspected:
                    self.last_event = ("Different identifier is plausible; score quarantined pending stable confirmation "
                                       f"({self._masked_identifier(possible)})")
                    self.score_armed = False
                    self.clear_count = 0
                    self.score_filter.reset()
                self.identifier_change_suspected = True
            changed = self.identifier_change_filter.add(reading)
            if changed is not None:
                if self._same_identifier(changed, active):
                    if self.identifier_change_suspected:
                        self.identifier_change_suspected = False
                        self.last_event = "Original identifier reconfirmed; ambiguous change cleared"
                else:
                    self._abandon_session(
                        f"stable identifier change to {self._masked_identifier(changed)}")
            return False
        if self.phase != Phase.WAITING_FOR_ID or not self.ready_for_id:
            return False
        raw = self.id_filter.add(reading)
        if raw is None:
            self.last_event = "Identifier rejected: invalid, low-confidence, or not yet stable"
            return False
        self.identifier = raw
        self.phase = Phase.ID_CAPTURED
        self.phase = Phase.WAITING_FOR_SCORE
        self.score_armed = False
        self.clear_count = 0
        self.score_filter.reset()
        self.identifier_change_filter.reset()
        self.identifier_change_suspected = False
        self.session_started_at = self._clock()
        self.last_event = (f"Accepted identifier {self._masked_identifier(raw)}; "
                           "waiting for a fresh score clear")
        return True

    def observe_score(self, reading: Reading) -> bool:
        if self.phase != Phase.WAITING_FOR_SCORE:
            return False
        if self._expire_session_if_needed():
            return False
        if self.identifier_change_suspected:
            self.score_filter.reset()
            return False
        if not self.score_armed:
            self.clear_count = self.clear_count + 1 if not reading.text.strip() else 0
            if self.clear_count >= self.config.stable_reads:
                self.score_armed = True
                self.last_event = "Fresh score armed after stable blank region"
            elif reading.text.strip():
                self.last_event = "Score quarantined until a fresh blank region is observed"
            return False
        raw = self.score_filter.add(reading)
        if raw is None:
            if reading.confidence < self.config.min_confidence:
                self.last_event = "Score rejected: confidence below configured minimum"
            elif parse_score(reading.text) is None:
                self.last_event = "Score rejected: OCR text is not a valid score"
            else:
                self.last_event = "Score reading is waiting for stable repeated frames"
            return False
        self.phase = Phase.SCORE_CAPTURED
        self.pending = {
            "submission_id": str(uuid.uuid4()), "event_id": self.config.event_id,
            "station_code": self.config.station_code, "profile_id": self.config.profile.profile_id,
            "profile_version": self.config.profile.profile_version,
            "raw_identifier": self.identifier, "raw_total_score": raw,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "diagnostic_metadata": {"helper": "windows_capture_v1"},
        }
        self.store.write({"phase": Phase.SUBMITTING.value, "pending": self.pending})
        self.phase = Phase.SUBMITTING
        self.last_event = "Score captured locally; queued with the accepted identifier"
        return True

    def submit_once(self) -> tuple[int, dict]:
        if self.phase != Phase.SUBMITTING or self.pending is None:
            raise RuntimeError("No capture is ready for submission")
        status, body = self.post(self.pending)
        self.last_result = {"http_status": status, "outcome": body.get("outcome", body.get("error", "unknown"))}
        if 200 <= status < 300:
            self.store.write({"phase": Phase.RESET.value, "last_result": self.last_result})
            self.pending = None
            self.identifier = None
            self.session_started_at = None
            self.phase = Phase.RESET
            self.score_filter.reset()
            self.id_filter.reset()
            self.identifier_change_filter.reset()
            self.identifier_change_suspected = False
            self.clear_count = 0
            self.ready_for_id = False
            self.last_event = "Submission accepted; waiting for score clear before the next session"
        else:
            self.last_event = "Submission failed or was rejected; queued capture remains pending"
        return status, body
