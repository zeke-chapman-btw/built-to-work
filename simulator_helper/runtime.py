"""Background polling loop and local no-submit diagnostic viewer."""
from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler

from .core import ApiClient, CaptureEngine, ConfigurationError, HelperConfig, Phase, StateStore, parse_identifier, parse_score
from .windows import WindowsCapture, gate_uncalibrated_score, read_region, score_calibration_progress


def configure_logging(path, verbose=False):
    logger = logging.getLogger("btw.simulator_helper")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def masked_identifier(raw):
    digits = "".join(character for character in raw if character.isdigit())
    return "***" + digits[-2:] if digits else "***"


def reset_requested():
    """Read the normal-run operator reset key without blocking the capture loop."""
    try:
        import msvcrt
        if not msvcrt.kbhit():
            return False
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0") and msvcrt.kbhit():
            msvcrt.getwch()
            return False
        return key.lower() == "r"
    except (ImportError, OSError):
        return False


class MemoryStateStore:
    """Diagnostic-only state store; captured pairs never touch disk."""

    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def read(self):
        return dict(self.data)

    def write(self, data):
        self.data = dict(data)


def run(config_path, verbose=False):
    config = HelperConfig.load(config_path)
    logger = configure_logging(config_path.parent / "helper.log", verbose)
    logger.info("Starting station=%s event=%s profile=%s version=%s interval=%.2fs",
                config.station_code, config.event_id, config.profile.profile_id,
                config.profile.profile_version, config.poll_interval)
    capture = WindowsCapture()
    try:
        capture.verify(config.profile)
        engine = CaptureEngine(config, StateStore(config.state_path), ApiClient(config).post)
        logger.info("Display calibrated; resumed phase=%s", engine.phase.value)
        logger.info("Press R to abandon an unscored session; pending submissions cannot be reset")
        last_phase = engine.phase
        last_event = engine.last_event
        while True:
            try:
                if reset_requested():
                    if engine.operator_reset():
                        logger.info("Operator reset: %s", engine.last_event)
                    else:
                        logger.info("Operator reset ignored in phase=%s; queued delivery state preserved",
                                    engine.phase.value)
                if engine.phase == Phase.WAITING_FOR_ID:
                    if not engine.ready_for_id:
                        reading = read_region(capture.grab(config.profile.score_region), "score",
                                              min_confidence=config.min_confidence,
                                              tesseract_cmd=config.tesseract_cmd)
                        ready = score_calibration_progress(config_path.parent / "score_ocr_calibration" / "examples")["qualified"]
                        reading = gate_uncalibrated_score(reading, ready)
                        engine.observe_score_clear(reading)
                    else:
                        reading = read_region(capture.grab(config.profile.identifier_region), "identifier",
                                              tesseract_cmd=config.tesseract_cmd)
                        if engine.observe_identifier(reading):
                            logger.info("Identifier captured %s", masked_identifier(engine.identifier))
                elif engine.phase == Phase.WAITING_FOR_SCORE:
                    id_reading = read_region(capture.grab(config.profile.identifier_region), "identifier",
                                              tesseract_cmd=config.tesseract_cmd)
                    engine.observe_identifier(id_reading)
                    if engine.phase == Phase.WAITING_FOR_SCORE:
                        score_reading = read_region(capture.grab(config.profile.score_region), "score",
                                                    min_confidence=config.min_confidence,
                                                    tesseract_cmd=config.tesseract_cmd)
                        ready = score_calibration_progress(config_path.parent / "score_ocr_calibration" / "examples")["qualified"]
                        score_reading = gate_uncalibrated_score(score_reading, ready)
                        if engine.observe_score(score_reading):
                            logger.info("Total Score captured; queued submission=%s", engine.pending["submission_id"])
                elif engine.phase == Phase.SUBMITTING:
                    submission_id = engine.pending["submission_id"]
                    logger.info("Submitting capture=%s", submission_id)
                    status, body = engine.submit_once()
                    if 200 <= status < 300:
                        logger.info("Delivered capture=%s outcome=%s replay=%s", submission_id,
                                    body.get("outcome"), body.get("idempotent_replay"))
                    elif status == 0 or status >= 500:
                        logger.warning("Retrying capture=%s after transient status=%s", submission_id, status)
                    else:
                        logger.error("Server rejected capture=%s status=%s error=%s; delivery preserved; stopping",
                                     submission_id, status, body.get("error", "unknown"))
                        raise RuntimeError("Permanent ingestion rejection; inspect state and server configuration")
                elif engine.phase == Phase.RESET:
                    reading = read_region(capture.grab(config.profile.score_region), "score",
                                          min_confidence=config.min_confidence,
                                          tesseract_cmd=config.tesseract_cmd)
                    ready = score_calibration_progress(config_path.parent / "score_ocr_calibration" / "examples")["qualified"]
                    reading = gate_uncalibrated_score(reading, ready)
                    if engine.observe_score_clear(reading):
                        logger.info("Score screen cleared; ready for next participant")
                if engine.phase != last_phase:
                    logger.info("State %s -> %s", last_phase.value, engine.phase.value)
                    last_phase = engine.phase
                if engine.last_event != last_event:
                    logger.info("Capture status: %s", engine.last_event)
                    last_event = engine.last_event
            except (OSError, RuntimeError, ValueError) as exc:
                if engine.phase == Phase.SUBMITTING and str(exc).startswith("Permanent ingestion rejection"):
                    raise
                logger.warning("Capture/OCR error in %s: %s", engine.phase.value, type(exc).__name__)
            time.sleep(config.poll_interval)
    finally:
        capture.close()


def diagnose(config_path):
    """Local live region preview. No API client, token, or official submission."""
    from PIL import ImageTk
    import tkinter as tk

    config = HelperConfig.load(config_path, require_token=False)
    capture = WindowsCapture()
    capture.verify(config.profile)
    root = tk.Tk()
    root.title("BTW Simulator Diagnostic - NO SUBMISSION")
    saved = StateStore(config.state_path).read()
    previous = saved.get("last_result")
    summary = "{} / {}".format(previous.get("http_status"), previous.get("outcome")) if isinstance(previous, dict) else "none"
    store = MemoryStateStore({"last_result": previous} if previous else None)
    engine = CaptureEngine(config, store, lambda _payload: (0, {"error": "diagnostic_no_submit"}))
    phase = tk.StringVar(value=engine.phase.value)
    last_result = tk.StringVar(value="Last submission: {} (diagnostic does not submit)".format(summary))
    id_text = tk.StringVar(value="Identifier: waiting")
    score_text = tk.StringVar(value="Total Score: waiting")
    session_text = tk.StringVar(value="Session: waiting for score clear")
    event_text = tk.StringVar(value=engine.last_event)
    tk.Label(root, text="LOCAL DIAGNOSTIC - NO OFFICIAL SUBMISSION", font=("Segoe UI", 16, "bold")).pack(pady=8)
    tk.Label(root, textvariable=phase, font=("Consolas", 13)).pack()
    tk.Label(root, textvariable=last_result).pack()
    tk.Label(root, textvariable=session_text, wraplength=1050).pack(pady=3)
    frames = tk.Frame(root)
    frames.pack(padx=15, pady=12)
    id_label = tk.Label(frames, text="Identifier crop")
    id_label.grid(row=0, column=0, padx=12)
    score_label = tk.Label(frames, text="Total Score crop")
    score_label.grid(row=0, column=1, padx=12)
    tk.Label(frames, textvariable=id_text, wraplength=600).grid(row=1, column=0)
    tk.Label(frames, textvariable=score_text, wraplength=600).grid(row=1, column=1)
    tk.Label(root, textvariable=event_text, wraplength=1050).pack(pady=3)

    def preview(label, image):
        reduced = image.copy()
        reduced.thumbnail((550, 300))
        if reduced.width < 250:
            reduced = reduced.resize((250, max(60, round(reduced.height * 250 / reduced.width))))
        photo = ImageTk.PhotoImage(reduced)
        label.configure(image=photo, text="")
        label.image = photo

    def update():
        try:
            id_image = capture.grab(config.profile.identifier_region)
            score_image = capture.grab(config.profile.score_region)
            preview(id_label, id_image)
            preview(score_label, score_image)
            identifier = read_region(id_image, "identifier", tesseract_cmd=config.tesseract_cmd)
            score = read_region(score_image, "score", min_confidence=config.min_confidence,
                                tesseract_cmd=config.tesseract_cmd)
            ready = score_calibration_progress(config_path.parent / "score_ocr_calibration" / "examples")["qualified"]
            score = gate_uncalibrated_score(score, ready)
            if engine.phase == Phase.WAITING_FOR_ID:
                if engine.ready_for_id:
                    engine.observe_identifier(identifier)
                else:
                    engine.observe_score_clear(score)
            elif engine.phase == Phase.WAITING_FOR_SCORE:
                engine.observe_identifier(identifier)
                if engine.phase == Phase.WAITING_FOR_SCORE:
                    engine.observe_score(score)
            elif engine.phase == Phase.RESET:
                engine.observe_score_clear(score)

            parsed_id = parse_identifier(identifier.text, config.profile.known_identifier_prefixes)
            id_view = masked_identifier(parsed_id) if parsed_id else "unrecognized/blank"
            id_filter = (engine.identifier_change_filter if engine.phase == Phase.WAITING_FOR_SCORE
                         else engine.id_filter)
            id_text.set(f"OCR: {id_view}  confidence: {identifier.confidence:.0f}  "
                        f"stable: {id_filter.count >= config.stable_reads}")
            score_text.set(f"Raw: {score.text!r}  confidence: {score.confidence:.0f}  "
                           f"valid: {parse_score(score.text) is not None}")
            if engine.phase == Phase.WAITING_FOR_SCORE:
                age = max(0.0, engine._clock() - engine.session_started_at)
                remaining = max(0.0, config.session_timeout_seconds - age)
                freshness = ("fresh/armed" if engine.score_armed else
                             f"waiting for {max(0, config.stable_reads - engine.clear_count)} blank reads")
                ambiguity = "; identifier change suspected" if engine.identifier_change_suspected else ""
                session_text.set(f"Accepted identifier: {masked_identifier(engine.identifier)}; "
                                 f"score freshness: {freshness}; session age: {age:.0f}s; "
                                 f"expires in: {remaining:.0f}s{ambiguity}")
            elif engine.phase == Phase.SUBMITTING:
                session_text.set("Local pending pair: "
                                 f"{masked_identifier(engine.pending.get('raw_identifier'))} + "
                                 f"{engine.pending.get('raw_total_score')} (NOT SUBMITTED)")
            elif engine.phase == Phase.RESET:
                session_text.set("Session reset: waiting for a fresh score clear before another identifier")
            else:
                session_text.set("Session: waiting for a fresh score clear before accepting an identifier")
            phase.set("SCORE_CAPTURED (LOCAL ONLY — NOT SUBMITTED)" if engine.phase == Phase.SUBMITTING
                      else engine.phase.value)
            event_text.set(engine.last_event)
        except Exception as exc:
            phase.set("Diagnostic error: " + type(exc).__name__)
        root.after(round(config.poll_interval * 1000), update)

    def reset_session():
        engine.operator_reset()
        event_text.set(engine.last_event)
        phase.set(engine.phase.value)

    def close():
        capture.close()
        root.destroy()

    tk.Button(root, text="Abandon / reset local session", command=reset_session).pack(pady=5)
    tk.Button(root, text="Close", command=close).pack(pady=5)
    root.protocol("WM_DELETE_WINDOW", close)
    update()
    root.mainloop()
