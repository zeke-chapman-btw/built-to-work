"""Background polling loop and local no-submit diagnostic viewer."""
from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler

from .core import ApiClient, CaptureEngine, ConfigurationError, HelperConfig, Phase, StateStore, StableReading, parse_identifier, parse_score
from .windows import WindowsCapture, read_region


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
        last_phase = engine.phase
        while True:
            try:
                if engine.phase == Phase.WAITING_FOR_ID:
                    if not engine.ready_for_id:
                        reading = read_region(capture.grab(config.profile.score_region), "score")
                        engine.observe_score_clear(reading)
                    else:
                        reading = read_region(capture.grab(config.profile.identifier_region), "identifier")
                        if engine.observe_identifier(reading):
                            logger.info("Identifier captured %s", masked_identifier(engine.identifier))
                elif engine.phase == Phase.WAITING_FOR_SCORE:
                    reading = read_region(capture.grab(config.profile.score_region), "score")
                    if engine.observe_score(reading):
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
                    reading = read_region(capture.grab(config.profile.score_region), "score")
                    if engine.observe_score_clear(reading):
                        logger.info("Score screen cleared; ready for next participant")
                if engine.phase != last_phase:
                    logger.info("State %s -> %s", last_phase.value, engine.phase.value)
                    last_phase = engine.phase
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
    phase = tk.StringVar(value="WAITING_FOR_ID")
    previous = StateStore(config.state_path).read().get("last_result")
    summary = "{} / {}".format(previous.get("http_status"), previous.get("outcome")) if isinstance(previous, dict) else "none"
    last_result = tk.StringVar(value="Last submission: {} (diagnostic does not submit)".format(summary))
    id_text = tk.StringVar(value="Identifier: waiting")
    score_text = tk.StringVar(value="Total Score: waiting")
    tk.Label(root, text="LOCAL DIAGNOSTIC - NO OFFICIAL SUBMISSION", font=("Segoe UI", 16, "bold")).pack(pady=8)
    tk.Label(root, textvariable=phase, font=("Consolas", 13)).pack()
    tk.Label(root, textvariable=last_result).pack()
    frames = tk.Frame(root)
    frames.pack(padx=15, pady=12)
    id_label = tk.Label(frames, text="Identifier crop")
    id_label.grid(row=0, column=0, padx=12)
    score_label = tk.Label(frames, text="Total Score crop")
    score_label.grid(row=0, column=1, padx=12)
    tk.Label(frames, textvariable=id_text, wraplength=600).grid(row=1, column=0)
    tk.Label(frames, textvariable=score_text, wraplength=600).grid(row=1, column=1)
    id_filter = StableReading(config.stable_reads, config.min_confidence,
                              lambda text: parse_identifier(text, config.profile.known_identifier_prefixes))
    score_filter = StableReading(config.stable_reads, config.min_confidence, parse_score)
    progress = {"phase": Phase.WAITING_FOR_ID, "blank": 0}

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
            identifier = read_region(id_image, "identifier")
            score = read_region(score_image, "score")
            stable_id = id_filter.add(identifier)
            stable_score = score_filter.add(score)
            id_text.set(f"Raw: {identifier.text!r}  confidence: {identifier.confidence:.0f}  stable: {bool(stable_id)}")
            score_text.set(f"Raw: {score.text!r}  confidence: {score.confidence:.0f}  valid: {parse_score(score.text) is not None}  stable: {bool(stable_score)}")
            if progress["phase"] == Phase.WAITING_FOR_ID and stable_id:
                progress["phase"] = Phase.WAITING_FOR_SCORE
                score_filter.reset()
            elif progress["phase"] == Phase.WAITING_FOR_SCORE and stable_score:
                progress["phase"] = Phase.SCORE_CAPTURED
            phase.set(progress["phase"].value)
        except Exception as exc:
            phase.set("Diagnostic error: " + type(exc).__name__)
        root.after(round(config.poll_interval * 1000), update)

    def close():
        capture.close()
        root.destroy()

    tk.Button(root, text="Reset diagnostic", command=lambda: progress.update(phase=Phase.WAITING_FOR_ID)).pack(pady=5)
    tk.Button(root, text="Close", command=close).pack(pady=5)
    root.protocol("WM_DELETE_WINDOW", close)
    update()
    root.mainloop()
