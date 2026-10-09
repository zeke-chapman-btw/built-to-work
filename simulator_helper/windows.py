"""Windows desktop region capture and fixed-region Tesseract recognition."""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .core import ConfigurationError, Reading, Rectangle

SCORE_OCR_PIPELINE_VERSION = "consensus-v3"
SCORE_CALIBRATION_REQUIRED_VALUES = 4


def resolve_tesseract(configured: str | None = None) -> str:
    """Resolve Tesseract independently of the process working directory."""
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            raise FileNotFoundError(f"Configured Tesseract path must be absolute: {configured}")
        if not configured_path.is_file():
            raise FileNotFoundError(f"Configured Tesseract executable not found: {configured_path}")
        return str(configured_path)

    env_command = os.environ.get("TESSERACT_CMD")
    if env_command:
        env_path = Path(env_command).expanduser()
        if env_path.is_absolute() and env_path.is_file():
            return str(env_path)

    candidates = []
    if os.name == "nt":
        for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
        candidates.append(Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
        candidates.append(Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    command = env_command if env_command and not Path(env_command).is_absolute() else "tesseract"
    found = shutil.which(command)
    if found:
        return str(Path(found).resolve())
    searched = ", ".join(str(path) for path in candidates) or "the executable search PATH"
    raise FileNotFoundError(
        f"Tesseract executable not found. Set tesseract_cmd in config to its absolute path; "
        f"checked {searched} and PATH command {command!r}."
    )


def _run_tesseract(command, *, input_data):
    options = {
        "input": input_data,
        "capture_output": True,
        "timeout": 8,
        "check": False,
    }
    if os.name == "nt":
        # CREATE_NO_WINDOW keeps each polling OCR process from flashing a console.
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        return subprocess.run(command, **options)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Could not launch Tesseract executable {command[0]!r}: {exc}") from exc


def set_dpi_awareness():
    if os.name != "nt":
        return
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except OSError:
        ctypes.windll.user32.SetProcessDPIAware()


class WindowsCapture:
    def __init__(self):
        if os.name != "nt":
            raise ConfigurationError("Live capture requires Windows")
        set_dpi_awareness()
        import mss
        self.session = mss.mss()
        self.virtual = dict(self.session.monitors[0])
        self.context = {
            "left": self.virtual["left"], "top": self.virtual["top"],
            "width": self.virtual["width"], "height": self.virtual["height"],
            "monitors": [
                {key: item[key] for key in ("left", "top", "width", "height")}
                for item in self.session.monitors[1:]
            ],
        }

    def verify(self, profile):
        if self.context != profile.display_context:
            raise ConfigurationError("Display topology differs from calibration; recalibrate before capture")

    def grab(self, rect: Rectangle):
        from PIL import Image
        if rect.x + rect.width > self.virtual["width"] or rect.y + rect.height > self.virtual["height"]:
            raise ConfigurationError("Capture rectangle falls outside the desktop")
        shot = self.session.grab({
            "left": self.virtual["left"] + rect.x,
            "top": self.virtual["top"] + rect.y,
            "width": rect.width, "height": rect.height,
        })
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def full_desktop(self):
        from PIL import Image
        shot = self.session.grab(self.virtual)
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def close(self):
        self.session.close()


def read_region(image, kind: str, *, tesseract_cmd: str | None = None,
                min_confidence: float = 70.0) -> Reading:
    """OCR one cropped rectangle. Score OCR requires preprocessing/PSM consensus."""
    if kind == "score":
        return analyze_score_image(image, min_confidence=min_confidence,
                                   tesseract_cmd=tesseract_cmd).reading
    from PIL import ImageOps
    image = ImageOps.autocontrast(ImageOps.grayscale(image))
    image = image.resize((image.width * 2, image.height * 2))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    executable = resolve_tesseract(tesseract_cmd)
    command = [executable, "stdin", "stdout",
               "--psm", "8" if kind == "identifier" else "7"]
    if kind != "identifier":
        command.extend(("-c", "tessedit_char_whitelist=0123456789.%"))
    command.append("tsv")
    result = _run_tesseract(command, input_data=stream.getvalue())
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or "no diagnostic output"
        raise RuntimeError(f"Tesseract executable {executable!r} exited with code {result.returncode}: {detail}")
    words = []
    confidences = []
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="	"):
        word = row.get("text", "").strip()
        if word:
            words.append(word)
            try:
                confidences.append(float(row.get("conf", "-1")))
            except ValueError:
                confidences.append(-1.0)
    return Reading(" ".join(words), min(confidences) if confidences else 0.0)


@dataclass(frozen=True)
class ScoreOCRAnalysis:
    reading: Reading
    parsed_score: str | None
    accepted: bool
    variants: tuple[dict, ...]


def _otsu_level(image):
    histogram = image.histogram()
    total = sum(histogram)
    weighted = sum(index * count for index, count in enumerate(histogram))
    background_weight = background_sum = 0
    best_variance, level = -1.0, 127
    for index, count in enumerate(histogram):
        background_weight += count
        if not background_weight:
            continue
        foreground_weight = total - background_weight
        if not foreground_weight:
            break
        background_sum += index * count
        mean_background = background_sum / background_weight
        mean_foreground = (weighted - background_sum) / foreground_weight
        variance = background_weight * foreground_weight * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance, level = variance, index
    return level


def _score_variants_at_scale(image, scale, *, resample=None):
    from PIL import ImageOps
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    resize_args = {}
    if resample is not None:
        resize_args["resample"] = resample
    gray = gray.resize((gray.width * scale, gray.height * scale), **resize_args)
    threshold = _otsu_level(gray)
    dark_text = gray.point(lambda pixel: 255 if pixel > threshold else 0)
    light_text = ImageOps.invert(dark_text)
    return (("grayscale", gray), ("threshold-dark", dark_text), ("threshold-light", light_text))


def _score_variants(image):
    """Preprocessing used for PSM 8 and its calibration preview (unchanged 3x)."""
    return _score_variants_at_scale(image, 3)


def score_preprocessing_variants(image):
    """Return named preprocessing previews for the local calibration window."""
    return _score_variants(image)


def _tesseract_words(image, psm, executable=None):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    command = [resolve_tesseract(executable), "stdin", "stdout",
               "--psm", str(psm), "-c", "tessedit_char_whitelist=0123456789.%", "tsv"]
    result = _run_tesseract(command, input_data=stream.getvalue())
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or "no diagnostic output"
        raise RuntimeError(f"Tesseract executable {command[0]!r} exited with code {result.returncode}: {detail}")
    words, confidences = [], []
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t"):
        word = row.get("text", "").strip()
        if word:
            words.append(word)
            try:
                confidences.append(float(row.get("conf", "-1")))
            except ValueError:
                confidences.append(-1.0)
    return " ".join(words), min(confidences) if confidences else 0.0


def analyze_score_image(image, *, min_confidence=70.0, tesseract_cmd=None) -> ScoreOCRAnalysis:
    """Require agreement across distinct preprocessing families and PSM outputs.

    Confidence is a filter, not proof of correctness; disagreement produces an
    empty low-confidence Reading so the capture state machine cannot accept it.
    PSM 7 uses a 2x Lanczos scale to retain thin decimal glyphs; PSM 8 keeps the
    existing 3x preprocessing that performed reliably on saved calibration crops.
    """
    from .core import parse_score

    from PIL import Image
    line_variants = dict(_score_variants_at_scale(
        image, 2, resample=Image.Resampling.LANCZOS))
    word_variants = dict(_score_variants(image))
    variants = []
    for family in ("grayscale", "threshold-dark", "threshold-light"):
        for psm, prepared in ((7, line_variants[family]), (8, word_variants[family])):
            raw, confidence = _tesseract_words(prepared, psm, tesseract_cmd)
            parsed = parse_score(raw)
            item = {"variant": family, "psm": psm, "raw": raw,
                    "confidence": confidence, "parsed": parsed}
            variants.append(item)

    qualified = [item for item in variants if item["confidence"] >= min_confidence]
    confident_scores = {item["parsed"] for item in qualified if item["parsed"] is not None}
    confident_malformed = any(item["raw"].strip() and item["parsed"] is None for item in qualified)
    family_votes = {}
    for family in {item["variant"] for item in variants}:
        votes = [item for item in qualified if item["variant"] == family and item["parsed"] is not None]
        if votes:
            family_votes[family] = votes[0]["parsed"]

    counts = {}
    for score in family_votes.values():
        counts[score] = counts.get(score, 0) + 1
    winner = next((score for score, count in counts.items() if count >= 2), None)
    if confident_malformed or len(confident_scores) > 1 or winner is None:
        evidence = " | ".join(item["raw"] for item in variants if item["raw"])
        return ScoreOCRAnalysis(Reading(evidence, 0.0), None, False, tuple(variants))

    agreeing = [item["confidence"] for item in qualified
                if item["parsed"] == winner and family_votes.get(item["variant"]) == winner]
    confidence = min(agreeing) if agreeing else 0.0
    return ScoreOCRAnalysis(Reading(winner, confidence), winner, True, tuple(variants))


def score_calibration_progress(examples_dir, *, required_values=SCORE_CALIBRATION_REQUIRED_VALUES):
    """Require four distinct, latest-per-value physical comparisons for this OCR pipeline."""
    latest = {}
    directory = Path(examples_dir)
    try:
        files = directory.glob("*.json")
        for path in files:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(item, dict):
                continue
            if item.get("pipeline_version") != SCORE_OCR_PIPELINE_VERSION:
                continue
            actual = str(item.get("actual_test_score", ""))
            timestamp = str(item.get("captured_at", ""))
            if not actual:
                continue
            previous = latest.get(actual)
            if previous is None or timestamp >= previous[0]:
                latest[actual] = (timestamp, bool(item.get("matches_actual")))
    except OSError:
        pass
    matching = sorted(score for score, (_timestamp, matched) in latest.items() if matched)
    mismatched = sorted(score for score, (_timestamp, matched) in latest.items() if not matched)
    return {"required_values": required_values, "matching_values": matching,
            "mismatched_values": mismatched,
            "qualified": len(matching) >= required_values and not mismatched}


def gate_uncalibrated_score(reading: Reading, qualified: bool) -> Reading:
    """Keep candidate text visible but force the state machine to reject until validated."""
    if not qualified and reading.text.strip():
        return Reading(reading.text, 0.0)
    return reading
