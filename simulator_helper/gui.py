"""Diagnostic-only Windows desktop monitor for the local simulator helper."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox, ttk

from .core import CaptureEngine, ConfigurationError, HelperConfig, Phase, Rectangle
from .runtime import MemoryStateStore, masked_identifier
from .windows import (SCORE_OCR_PIPELINE_VERSION, SCORE_CALIBRATION_REQUIRED_VALUES,
                      WindowsCapture, analyze_score_image, gate_uncalibrated_score,
                      read_region, score_calibration_progress, score_preprocessing_variants)
from .xml_results import XMLMonitorWorker, XMLResultRepository

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.diagnostic.json"
HISTORY_PATH = APP_DIR / "diagnostic_history.json"
HISTORY_LIMIT = 200


def compare_score_values(actual_text, analysis):
    from decimal import Decimal
    from .core import parse_score
    actual = parse_score(actual_text)
    if actual is None:
        raise ValueError("Enter a score from 0 to 100 with at most two decimal places")
    return bool(analysis and analysis.accepted and Decimal(analysis.parsed_score) == Decimal(actual))


def _local_time(value: str | None = None) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S") if value else ""
    except (ValueError, TypeError):
        return ""


def _display_result_time(value: str | None = None, *, now: datetime | None = None) -> str:
    """Use compact local time for today's results and include the date for older ones."""
    try:
        moment = datetime.fromisoformat(value).astimezone() if value else None
        if moment is None:
            return ""
        current = (now or datetime.now().astimezone()).astimezone()
        clock = moment.strftime("%I:%M %p").lstrip("0")
        if moment.date() == current.date():
            return clock
        return f"{moment.strftime('%b %d, %Y')} {clock}"
    except (ValueError, TypeError):
        return ""


class DiagnosticHistory:
    """Small, masked-only diagnostic record; never contains tokens or raw IDs."""
    def __init__(self, path: Path = HISTORY_PATH):
        self.path = Path(path)
        self.rows = self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [row for row in data[-HISTORY_LIMIT:] if isinstance(row, dict)]
        except (OSError, ValueError):
            pass
        return []

    def append(self, row: dict):
        safe = {key: row.get(key) for key in ("time", "identifier", "score", "status", "delivery")}
        if self.rows and self.rows[-1].get("capture_id") == row.get("capture_id"):
            return False
        safe["capture_id"] = row.get("capture_id")
        self.rows.append(safe)
        self.rows = self.rows[-HISTORY_LIMIT:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rows, indent=2), encoding="utf-8")
        return True

    def clear_current_view(self):
        """Start a fresh on-screen view while preserving the local diagnostic record."""
        return []


class MonitorWorker(threading.Thread):
    """Single capture worker. No production API client is imported or called."""
    def __init__(self, config_path: Path, events: queue.Queue, *, capture_factory=WindowsCapture):
        super().__init__(name="BTW-Diagnostic-Capture", daemon=False)
        self.config_path, self.events, self.capture_factory = Path(config_path), events, capture_factory
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def emit(self, kind, **data):
        self.events.put((kind, data))

    def run(self):
        capture = None
        try:
            config = HelperConfig.load(self.config_path, require_token=False)
            # The configured durable submission file is deliberately never opened.
            engine = CaptureEngine(config, MemoryStateStore(), lambda _payload: (_ for _ in ()).throw(
                AssertionError("Diagnostic mode must never submit")))
            capture = self.capture_factory()
            capture.verify(config.profile)
            self.emit("configured", config=config, timestamp=_local_time(datetime.now().astimezone().isoformat()))
            last_ocr = None
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    id_image = capture.grab(config.profile.identifier_region)
                    score_image = capture.grab(config.profile.score_region)
                    self.emit("capture_ok", time=_local_time(datetime.now().astimezone().isoformat()))
                    last_ocr = _local_time(datetime.now().astimezone().isoformat())
                    self.emit("ocr_poll", time=last_ocr)
                    id_reading = read_region(id_image, "identifier", tesseract_cmd=config.tesseract_cmd)
                    score_reading = read_region(score_image, "score", min_confidence=config.min_confidence,
                                                tesseract_cmd=config.tesseract_cmd)
                    progress = score_calibration_progress(self.config_path.parent / "score_ocr_calibration" / "examples")
                    score_reading = gate_uncalibrated_score(score_reading, progress["qualified"])
                    if engine.phase == Phase.WAITING_FOR_ID:
                        if engine.ready_for_id:
                            engine.observe_identifier(id_reading)
                        else:
                            engine.observe_score_clear(score_reading)
                    elif engine.phase == Phase.WAITING_FOR_SCORE:
                        engine.observe_identifier(id_reading)
                        if engine.phase == Phase.WAITING_FOR_SCORE:
                            engine.observe_score(score_reading)
                    elif engine.phase == Phase.RESET:
                        engine.observe_score_clear(score_reading)
                    if engine.phase == Phase.SUBMITTING and engine.pending:
                        payload = engine.pending
                        timestamp = payload.get("captured_at")
                        self.emit("captured", row={
                            "capture_id": payload.get("submission_id"), "time": _local_time(timestamp),
                            "identifier": masked_identifier(payload.get("raw_identifier")),
                            "score": payload.get("raw_total_score"), "status": "Captured Locally",
                            "delivery": "Diagnostic only — not submitted"})
                        # A fresh engine re-enters the score-clear interlock. This drops
                        # only the in-memory diagnostic pair and never touches state.json.
                        engine = CaptureEngine(config, MemoryStateStore(), lambda _payload: (0, {}))
                    session_age = None
                    if engine.session_started_at is not None:
                        session_age = max(0, int(engine._clock() - engine.session_started_at))
                    self.emit("reading", phase=engine.phase.value, event=engine.last_event,
                              identifier=masked_identifier(engine.identifier) if engine.identifier else "—",
                              session_age=session_age, timeout=config.session_timeout_seconds,
                              identifier_confidence=id_reading.confidence,
                              score_confidence=score_reading.confidence,
                              calibration_progress=progress,
                              score=engine.pending.get("raw_total_score") if engine.pending else None,
                              identifier_change=engine.identifier_change_suspected,
                              last_ocr=last_ocr)
                except Exception as exc:
                    self.emit("capture_error", message=f"{type(exc).__name__}: {exc}")
                delay = max(0, config.poll_interval - (time.monotonic() - started))
                self.stop_event.wait(delay)
        except Exception as exc:
            self.emit("fatal", message=f"{type(exc).__name__}: {exc}")
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception as exc:
                    self.emit("capture_error", message=f"Capture close failed: {type(exc).__name__}")
            self.emit("stopped")


class MonitorApp:
    def __init__(self, root: tk.Tk, *, config_path: Path = CONFIG_PATH, history_path: Path = HISTORY_PATH,
                 worker_factory=MonitorWorker):
        self.root, self.config_path = root, Path(config_path)
        self.history = DiagnosticHistory(history_path)
        self.worker_factory = worker_factory
        self.events: queue.Queue = queue.Queue()
        self.worker = None
        self.closing = False
        self.last_success = None
        self.last_success_monotonic = None
        self.last_ocr = None
        self.started_at = None
        self.rows = []
        self.logger = logging.getLogger("btw.simulator_monitor")
        self._build()
        self._poll_events()
        self._refresh_health()

    def _build(self):
        self.root.title("BTW Simulator Monitor")
        self.root.geometry("960x720")
        self.root.minsize(820, 620)
        self.root.configure(bg="#f2f5f8")
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#12314a")
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 11, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 14, "bold"))
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        # Keep child content requests from resizing the top-level. The user can
        # still resize the window; the outer frame simply follows that size.
        outer.pack_propagate(False)
        ttk.Label(outer, text="BTW Simulator Monitor", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Built to Work  •  Local simulator capture").pack(anchor="w", pady=(0, 12))
        ttk.Label(outer, text="DIAGNOSTIC MODE — No scores are being sent to BTW",
                  background="#fff1cc", foreground="#6b4700", padding=9,
                  font=("Segoe UI", 11, "bold")).pack(fill="x", pady=(0, 12))

        cards = ttk.Frame(outer)
        cards.pack(fill="x")
        self.status_var = tk.StringVar(value="●  Capture Stopped")
        self.phase_var = tk.StringVar(value="Waiting to start")
        self.id_var = tk.StringVar(value="—")
        self.age_var = tk.StringVar(value="—")
        self.score_var = tk.StringVar(value="No score captured")
        self.freshness_var = tk.StringVar(value="Freshness: waiting")
        for col, (title, var) in enumerate((("Capture status", self.status_var), ("Current participant", self.id_var),
                                             ("Current score", self.score_var))):
            frame = ttk.LabelFrame(cards, text=title, style="Section.TLabelframe", padding=12)
            frame.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0), pady=2)
            frame.configure(height=112)
            frame.grid_propagate(False)
            ttk.Label(frame, textvariable=var, style="Status.TLabel", wraplength=260).pack(anchor="w")
            if col == 0:
                ttk.Label(frame, textvariable=self.phase_var).pack(anchor="w", pady=(6, 0))
            elif col == 1:
                ttk.Label(frame, textvariable=self.age_var).pack(anchor="w", pady=(6, 0))
            else:
                ttk.Label(frame, textvariable=self.freshness_var).pack(anchor="w", pady=(6, 0))
        for col in range(3):
            cards.columnconfigure(col, weight=1)

        activity = ttk.LabelFrame(outer, text="Capture activity", style="Section.TLabelframe", padding=10)
        activity.pack(fill="x", pady=(12, 8))
        self.activity_var = tk.StringVar(value="Capture has not started.")
        self.warning_var = tk.StringVar(value="")
        self.event_var = tk.StringVar(value="")
        status_body = ttk.Frame(activity)
        status_body.pack(fill="x")
        self.activity_text = tk.Text(status_body, height=4, wrap="word", state="disabled",
                                     font=("Segoe UI", 10), relief="flat", borderwidth=0,
                                     highlightthickness=0, takefocus=False)
        self.activity_text.tag_configure("warning", foreground="#9c3500")
        self.activity_text.pack(side="left", fill="x", expand=True)
        status_scroll = ttk.Scrollbar(status_body, orient="vertical", command=self.activity_text.yview)
        status_scroll.pack(side="right", fill="y")
        self.activity_text.configure(yscrollcommand=status_scroll.set)
        for variable in (self.activity_var, self.warning_var, self.event_var):
            variable.trace_add("write", self._render_activity_panel)
        self._render_activity_panel()

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(2, 10))
        self.start_button = ttk.Button(controls, text="Start Capture", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop Capture", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Button(controls, text="Reset Current Session", command=self.reset).pack(side="left", padx=6)
        ttk.Button(controls, text="Score OCR Calibration", command=self.show_score_calibration).pack(side="left", padx=6)
        ttk.Button(controls, text="Open Settings", command=self.show_settings).pack(side="right")
        ttk.Button(controls, text="New Monitoring Session", command=self.new_view).pack(side="right", padx=6)
        ttk.Button(controls, text="View History", command=self.show_history).pack(side="right", padx=6)

        history_frame = ttk.LabelFrame(outer, text="Diagnostic score history", style="Section.TLabelframe", padding=8)
        history_frame.pack(fill="both", expand=True)
        ttk.Label(history_frame, text="Local diagnostic captures only; official submissions are not shown here.").pack(anchor="w")
        columns = ("time", "identifier", "score", "status", "delivery")
        self.tree = ttk.Treeview(history_frame, columns=columns, show="headings", height=9)
        for col, label, width in (("time", "Time", 155), ("identifier", "Ticket", 110), ("score", "Score", 90),
                                  ("status", "Capture status", 150), ("delivery", "Delivery", 350)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(5, 0))
        for row in reversed(self.history.rows):
            self._insert_history(row)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _insert_history(self, row):
        self.tree.insert("", 0, values=tuple(row.get(key, "") for key in ("time", "identifier", "score", "status", "delivery")))

    def _render_activity_panel(self, *_args):
        """Update a fixed-height, scrollable status area in place."""
        text = self.activity_text
        old_position = text.yview()[0]
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", f"Activity: {self.activity_var.get()}\n")
        warning = self.warning_var.get() or "—"
        if self.warning_var.get():
            text.insert("end", f"Warning: {warning}\n", "warning")
        else:
            text.insert("end", f"Warning: {warning}\n")
        text.insert("end", f"Detail: {self.event_var.get() or '—'}")
        text.configure(state="disabled")
        text.yview_moveto(old_position)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            # Validate before marking active; worker repeats validation and capture verification.
            config = HelperConfig.load(self.config_path, require_token=False)
            self.worker = self.worker_factory(self.config_path, self.events)
            self.started_at = time.monotonic()
            self.last_success = self.last_ocr = None
            self.last_success_monotonic = None
            self.warning_var.set("")
            self.activity_var.set("Starting screen capture and OCR…")
            self.status_var.set("●  Starting")
            self.phase_var.set(f"Polling every {config.poll_interval:g}s")
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.worker.start()
        except Exception as exc:
            self.logger.exception("Unable to start diagnostic capture")
            self.status_var.set("●  Error")
            self.activity_var.set(f"Unable to start: {type(exc).__name__}: {exc}")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")

    def stop(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.status_var.set("●  Stopping")
            self.activity_var.set("Stopping after the current screen/OCR read…")
            self.stop_button.configure(state="disabled")

    def reset(self):
        active = self.worker and self.worker.is_alive()
        if active and not messagebox.askyesno("Abandon current session?",
                "This abandons the current identifier and any unscored local association. Continue?", parent=self.root):
            return
        if active:
            self.worker.stop()
            self.pending_reset = True
            self.activity_var.set("Reset requested; capture will restart after the current read finishes.")
            self.root.after(100, self._finish_reset)
        else:
            messagebox.showinfo("Reset session", "No active capture session. Start Capture to begin a fresh diagnostic session.", parent=self.root)

    def _finish_reset(self):
        if not getattr(self, "pending_reset", False):
            return
        if self.worker and self.worker.is_alive():
            self.root.after(100, self._finish_reset)
            return
        self.pending_reset = False
        self.start()

    def new_view(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Monitoring view", "Stop capture before starting a new monitoring view.", parent=self.root)
            return
        self.rows = self.history.clear_current_view()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.start()

    def show_history(self):
        window = tk.Toplevel(self.root)
        window.title("Diagnostic Capture History")
        window.geometry("860x420")
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Saved diagnostic captures — local only, never submitted").pack(anchor="w", pady=(0, 8))
        columns = ("time", "identifier", "score", "status", "delivery")
        table = ttk.Treeview(frame, columns=columns, show="headings")
        for col, label, width in (("time", "Time", 155), ("identifier", "Ticket", 100), ("score", "Score", 90),
                                  ("status", "Capture status", 145), ("delivery", "Delivery", 330)):
            table.heading(col, text=label)
            table.column(col, width=width, anchor="w")
        table.pack(fill="both", expand=True)
        for row in reversed(self.history.rows):
            table.insert("", "end", values=tuple(row.get(key, "") for key in columns))
        ttk.Button(frame, text="Close", command=window.destroy).pack(anchor="e", pady=(8, 0))

    def show_score_calibration(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Score OCR Calibration", "Stop capture before opening score calibration.", parent=self.root)
            return
        ScoreCalibrationDialog(self.root, self.config_path)

    def show_settings(self):
        try:
            config = HelperConfig.load(self.config_path, require_token=False)
            profile = config.profile
            text = (f"Active configuration: {self.config_path}\n"
                    f"Active profile: {self.config_path.parent / json.loads(self.config_path.read_text(encoding='utf-8'))['profile_path']}\n\n"
                    f"Display: {profile.expected_width} × {profile.expected_height}\n"
                    f"Identifier region: {profile.identifier_region.as_dict()}\n"
                    f"Score region: {profile.score_region.as_dict()}\n"
                    f"Identifier confidence minimum: {config.identifier_min_confidence:g}\n"
                    f"Score confidence minimum: {config.min_confidence:g}\n"
                    f"Stable reads: {config.stable_reads}\nPolling interval: {config.poll_interval:g}s\n"
                    f"Session timeout: {config.session_timeout_seconds:g}s\n\nSettings are read-only in this monitor.")
        except Exception as exc:
            text = f"Settings could not be loaded: {type(exc).__name__}: {exc}"
        messagebox.showinfo("Read-only Settings", text, parent=self.root)

    def _poll_events(self):
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "configured":
                self.activity_var.set("Display topology matches the calibrated profile. Capture and OCR polling are active.")
            elif kind == "capture_ok":
                self.last_success = data["time"]
                self.last_success_monotonic = time.monotonic()
            elif kind == "ocr_poll":
                self.last_ocr = data["time"]
            elif kind == "reading":
                self.last_ocr = data.get("last_ocr")
                self.phase_var.set(data["phase"].replace("_", " ").title())
                self.id_var.set(data["identifier"])
                self.age_var.set("—" if data["session_age"] is None else
                    f"Session age {data['session_age']}s  •  timeout {max(0, int(data['timeout'] - data['session_age']))}s")
                if data["identifier_change"]:
                    self.warning_var.set("Identifier change suspected; score readings are quarantined.")
                elif not data["calibration_progress"]["qualified"]:
                    progress = data["calibration_progress"]
                    self.warning_var.set(f"Needs Review: score OCR calibration is {len(progress['matching_values'])}/"
                        f"{progress['required_values']} distinct physical test scores; score capture is blocked.")
                elif data["event"].lower().find("abandon") >= 0:
                    self.warning_var.set(data["event"])
                else:
                    self.warning_var.set("")
                self.event_var.set(f"{data['event']}  |  OCR confidence: ticket {data['identifier_confidence']:.0f}, score {data['score_confidence']:.0f}")
                # The phase can legitimately change; keep the headline stable
                # and show the current phase in its reserved line below.
                self.status_var.set("●  Capture Running")
            elif kind == "captured":
                row = data["row"]
                if self.history.append(row):
                    self.rows.append(row)
                    self._insert_history(row)
                self.score_var.set(f"{row['score']}  •  {row['time']}")
                self.freshness_var.set("Accepted stable score — diagnostic only")
            elif kind == "capture_error":
                self.logger.error("Capture/OCR error: %s", data["message"])
                self.warning_var.set("Capture/OCR error: " + data["message"])
            elif kind == "fatal":
                self.logger.error("Capture could not start: %s", data["message"])
                self.status_var.set("●  Error")
                self.activity_var.set("Capture could not start: " + data["message"])
            elif kind == "stopped":
                self.status_var.set("●  Capture Stopped")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                if not self.closing:
                    self.activity_var.set(self._activity_text())
                if self.closing:
                    self.root.destroy()
                    return
                if getattr(self, "pending_reset", False):
                    self._finish_reset()
        if not self.closing:
            self.root.after(100, self._poll_events)

    def _activity_text(self):
        return f"Last successful screen capture: {self.last_success or 'not yet captured'}  •  Last OCR poll: {self.last_ocr or 'not yet polled'}"

    def _refresh_health(self):
        if self.worker and self.worker.is_alive():
            self.activity_var.set(self._activity_text())
            if self.last_success_monotonic is None:
                if time.monotonic() - self.started_at > 25:
                    self.warning_var.set("No successful screen capture yet. Check display/profile configuration.")
            elif time.monotonic() - self.last_success_monotonic > 25:
                self.warning_var.set("Capture appears stalled; latest successful reading may be stale.")
        if not self.closing:
            self.root.after(1000, self._refresh_health)

    def close(self):
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.worker.stop()
            self.status_var.set("●  Stopping safely")
        else:
            self.root.destroy()


class ScoreCalibrationDialog:
    """Capture, compare and save score-only OCR examples in local diagnostic mode."""
    def __init__(self, parent, config_path=CONFIG_PATH, *, capture_factory=WindowsCapture,
                 analysis_func=analyze_score_image, examples_dir=None):
        self.parent, self.config_path = parent, Path(config_path)
        self.capture_factory, self.analysis_func = capture_factory, analysis_func
        self.examples_dir = Path(examples_dir or APP_DIR / "score_ocr_calibration" / "examples")
        self.events = queue.Queue()
        self.image = None
        self.analysis = None
        self.photo = None
        self.context_photo = None
        self.compare_result = None
        self.config = HelperConfig.load(self.config_path, require_token=False)
        self.window = tk.Toplevel(parent)
        self.window.title("Score OCR Calibration — Diagnostic Only")
        self.window.geometry("900x760")
        self.window.minsize(720, 650)
        self._build()
        self._refresh_calibration_progress()
        self.window.after(100, self._poll)

    def _build(self):
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Score OCR Calibration", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="TEST DATA ONLY — captures only the calibrated score region; nothing is submitted.",
                  foreground="#785000", wraplength=750).pack(anchor="w", pady=(4, 10))
        self.region_var = tk.StringVar(value=f"Region {self.config.profile.score_region.as_dict()}  •  "
            f"Display {self.config.profile.expected_width}×{self.config.profile.expected_height}  •  "
            f"Score threshold {self.config.min_confidence:g}")
        ttk.Label(frame, textvariable=self.region_var).pack(anchor="w", pady=(0, 8))
        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        self.capture_button = ttk.Button(controls, text="Capture Score Region", command=self.capture)
        self.capture_button.pack(side="left")
        self.status_var = tk.StringVar(value="Ready. Show a known test score in the simulator first.")
        ttk.Label(controls, textvariable=self.status_var, wraplength=500).pack(side="left", padx=12)

        self.preview = ttk.Label(frame, text="No image captured", anchor="center", relief="groove")
        self.preview.pack(fill="x", pady=(10, 6), ipady=18)
        self.context_preview = ttk.Label(frame, text="Nearby screen context preview", anchor="center", relief="groove")
        self.context_preview.pack(fill="x", pady=(0, 6), ipady=4)
        self.variant_frame = ttk.Frame(frame)
        self.variant_frame.pack(fill="x", pady=(0, 6))
        self.variant_photos = []
        self.variants_var = tk.StringVar(value="Preprocessing previews appear after capture.")
        ttk.Label(frame, textvariable=self.variants_var, wraplength=760).pack(anchor="w", pady=(0, 8))
        self.calibration_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.calibration_var, foreground="#785000", wraplength=760).pack(anchor="w", pady=(0, 6))
        self.raw_var = tk.StringVar(value="Raw OCR: —")
        self.parsed_var = tk.StringVar(value="Parsed numeric score: —")
        self.confidence_var = tk.StringVar(value="Confidence: —")
        ttk.Label(frame, textvariable=self.raw_var, wraplength=760).pack(anchor="w")
        ttk.Label(frame, textvariable=self.parsed_var).pack(anchor="w")
        ttk.Label(frame, textvariable=self.confidence_var).pack(anchor="w")
        ttk.Label(frame, text="Confidence is an OCR estimate, not proof that the score is correct.",
                  foreground="#785000").pack(anchor="w")
        actual_row = ttk.Frame(frame)
        actual_row.pack(fill="x", pady=(10, 2))
        ttk.Label(actual_row, text="Actual score shown (test score):").pack(side="left")
        self.actual_entry = ttk.Entry(actual_row, width=14)
        self.actual_entry.pack(side="left", padx=8)
        self.compare_button = ttk.Button(actual_row, text="Compare", command=self.compare, state="disabled")
        self.compare_button.pack(side="left")
        self.save_button = ttk.Button(actual_row, text="Save Diagnostic Example", command=self.save_example, state="disabled")
        self.save_button.pack(side="left", padx=8)
        self.result_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.result_var, font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=4)
        ttk.Label(frame, text=f"Examples are stored locally in: {self.examples_dir}", wraplength=760).pack(anchor="w", pady=(8, 0))

    def capture(self):
        self.capture_button.configure(state="disabled")
        self.compare_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.status_var.set("Capturing calibrated score region and running OCR variants…")
        self.result_var.set("")
        self.compare_result = None
        threading.Thread(target=self._capture_worker, name="Score-OCR-Calibration", daemon=True).start()

    def _capture_worker(self):
        capture = None
        try:
            capture = self.capture_factory()
            capture.verify(self.config.profile)
            region = self.config.profile.score_region
            margin = 12
            left, top = max(0, region.x - margin), max(0, region.y - margin)
            right = min(self.config.profile.expected_width, region.x + region.width + margin)
            bottom = min(self.config.profile.expected_height, region.y + region.height + margin)
            context = capture.grab(Rectangle(left, top, right - left, bottom - top))
            image = context.crop((region.x - left, region.y - top,
                                  region.x - left + region.width, region.y - top + region.height))
            analysis = self.analysis_func(image, min_confidence=self.config.min_confidence,
                                          tesseract_cmd=self.config.tesseract_cmd)
            self.events.put(("ok", image, analysis, context))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    pass

    def _poll(self):
        try:
            event = self.events.get_nowait()
        except queue.Empty:
            self.window.after(100, self._poll)
            return
        if event[0] == "error":
            self.status_var.set("Capture failed; check the live desktop and display profile.")
            self.raw_var.set("Error: " + event[1])
        else:
            _, self.image, self.analysis, context = event
            from PIL import ImageTk
            thumbnail = self.image.copy()
            thumbnail.thumbnail((760, 230))
            if thumbnail.width < 250:
                thumbnail = thumbnail.resize((min(760, thumbnail.width * 3), max(80, thumbnail.height * 3)))
            self.photo = ImageTk.PhotoImage(thumbnail)
            self.preview.configure(image=self.photo, text="")
            self.preview.image = self.photo
            context_preview = context.copy()
            context_preview.thumbnail((760, 110))
            self.context_photo = ImageTk.PhotoImage(context_preview)
            self.context_preview.configure(image=self.context_photo, text="")
            self.context_preview.image = self.context_photo
            for child in self.variant_frame.winfo_children():
                child.destroy()
            self.variant_photos.clear()
            for column, (name, variant_image) in enumerate(score_preprocessing_variants(self.image)):
                variant_image.thumbnail((220, 86))
                photo = ImageTk.PhotoImage(variant_image)
                self.variant_photos.append(photo)
                variant_label = ttk.Label(self.variant_frame, text=name, image=photo, compound="top", anchor="center")
                variant_label.grid(row=0, column=column, sticky="nsew", padx=4)
                self.variant_frame.columnconfigure(column, weight=1)
            accepted = self.analysis.accepted
            raw = "; ".join(f"{v['variant']}/PSM{v['psm']}: {v['raw']!r} [{v['confidence']:.0f}]"
                            for v in self.analysis.variants)
            self.raw_var.set("Raw OCR variants: " + (raw or "(no text)"))
            self.parsed_var.set("Parsed numeric score: " + (self.analysis.parsed_score if accepted else "Ambiguous / rejected"))
            self.confidence_var.set(f"Consensus confidence: {self.analysis.reading.confidence:.0f}  •  "
                                    f"Acceptance requires agreement across preprocessing families and PSMs")
            self.variants_var.set("Variants: grayscale and Otsu dark/light families; PSM 7 uses 2× Lanczos, PSM 8 uses existing 3× scaling.")
            self.status_var.set(f"Captured {self.image.width}×{self.image.height}. Compare with the value visible in the simulator.")
            qualified = self._refresh_calibration_progress()
            if accepted and not qualified:
                self.parsed_var.set(f"Parsed numeric score: {self.analysis.parsed_score} (Needs Review; calibration incomplete)")
            self.compare_button.configure(state="normal")
        self.capture_button.configure(state="normal")
        self.window.after(100, self._poll)

    def compare(self):
        from .core import parse_score
        actual = parse_score(self.actual_entry.get())
        if actual is None:
            messagebox.showerror("Invalid test score", "Enter a score from 0 to 100 with at most two decimal places.", parent=self.window)
            return
        self.compare_result = compare_score_values(actual, self.analysis)
        if self.compare_result:
            self.result_var.set("MATCH — OCR equals the entered test score")
            self.save_button.configure(state="normal")
        elif self.analysis and self.analysis.accepted:
            self.result_var.set(f"MISMATCH — OCR {self.analysis.parsed_score}; actual {actual}")
            self.save_button.configure(state="normal")
        else:
            self.result_var.set(f"OCR REJECTED / AMBIGUOUS — actual test score {actual}")
            self.save_button.configure(state="normal")

    def save_example(self):
        from .core import parse_score
        actual = parse_score(self.actual_entry.get())
        if not self.image or not self.analysis or actual is None or self.compare_result is None:
            messagebox.showerror("Compare first", "Capture an image, enter its actual score, and compare before saving.", parent=self.window)
            return
        import uuid
        from datetime import timezone
        self.examples_dir.mkdir(parents=True, exist_ok=True)
        case_id = uuid.uuid4().hex
        image_path = self.examples_dir / f"{case_id}.png"
        metadata_path = self.examples_dir / f"{case_id}.json"
        self.image.save(image_path, format="PNG")
        metadata = {
            "pipeline_version": SCORE_OCR_PIPELINE_VERSION,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "actual_test_score": actual,
            "ocr_accepted": self.analysis.accepted,
            "parsed_score": self.analysis.parsed_score,
            "matches_actual": self.compare_result,
            "consensus_confidence": self.analysis.reading.confidence,
            "variants": list(self.analysis.variants),
            "settings": {
                "profile_version": self.config.profile.profile_version,
                "display": {"width": self.config.profile.expected_width, "height": self.config.profile.expected_height},
                "score_region": self.config.profile.score_region.as_dict(),
                "score_min_confidence": self.config.min_confidence,
                "stable_reads": self.config.stable_reads,
                "preprocessing": [
                    "PSM7: grayscale-autocontrast-and-Otsu-2x-Lanczos",
                    "PSM8: grayscale-autocontrast-and-Otsu-3x-default",
                ],
                "tesseract_psm": [7, 8],
            },
            "image_file": image_path.name,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self.status_var.set(f"Saved test example {case_id[:8]} locally.")
        self._refresh_calibration_progress()

    def _refresh_calibration_progress(self):
        progress = score_calibration_progress(self.examples_dir)
        matches, failures = progress["matching_values"], progress["mismatched_values"]
        self.calibration_var.set(
            f"Physical calibration: {len(matches)}/{progress['required_values']} distinct scores match; "
            f"latest mismatches requiring a passing retest: {', '.join(failures) if failures else 'none'}. "
            + ("Score OCR qualified for local capture." if progress["qualified"] else
               "Scores remain Needs Review and are blocked from capture until qualified."))
        return progress["qualified"]


def _display_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value


def _masked_ticket(value: str | None) -> str:
    return f"••••••{value[-4:]}" if value else "—"


def _xml_scan_status_message(data: dict) -> str:
    if not data.get("directory_exists"):
        return (data.get("error") or "XML source directory unavailable.") + " Monitoring will retry automatically."
    if data.get("errors"):
        return "Some files need retry: " + "  •  ".join(data["errors"][:3])
    return f"XML scan successful; {data.get('files_seen', 0)} XML file(s) present."


def _xml_result_status_message(record: dict) -> str:
    return (f"XML detected and archived: {record.get('source_filename', 'scorecard.xml')}  •  "
            f"{record.get('exercise') or 'Exercise needs review'}  •  "
            f"{record.get('validation_status', 'unknown')}  •  Overall score: Pending Formula")


class XMLResultDetailsDialog:
    """Scrollable local review of one archived simulator result."""
    def __init__(self, parent, record: dict):
        self.record = record
        self.window = tk.Toplevel(parent)
        self.window.title("XML Result Details — Local Review")
        self.window.geometry("780x620")
        self.window.minsize(620, 420)
        outer = ttk.Frame(self.window, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Result details", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        summary = ttk.Frame(outer)
        summary.pack(fill="x", pady=(10, 8))
        lines = (
            ("Ticket", record.get("ticket") or "Missing or invalid"),
            ("Exercise", record.get("exercise") or "Unidentified — review"),
            ("Failure status", record.get("failure_status") or "Unknown"),
            ("Failure reason", record.get("failure_reason") or "—"),
            ("Laps completed", record.get("header", {}).get("LapsCompleted", "—")),
            ("Other header data", json.dumps({key: value for key, value in record.get("header", {}).items()
                                               if key not in {"Failure", "FailureReason", "FailureCode", "LapsCompleted", "LoginCode"}},
                                              ensure_ascii=False) or "—"),
            ("Overall score", "Pending Formula"),
            ("Import status", f"{record.get('archive_status')} / {record.get('validation_status')}"),
            ("Original file", record.get("source_filename", "")),
            ("Original path", record.get("source_path", "")),
            ("Archive path", record.get("archive_path", "")),
            ("SHA-256", record.get("sha256", "")),
            ("Captured locally", _display_time(record.get("imported_at"))),
            ("Formula version", record.get("scoring_formula_version") or "Not available"),
        )
        for row, (label, value) in enumerate(lines):
            ttk.Label(summary, text=label + ":", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
            ttk.Label(summary, text=value, wraplength=650).grid(row=row, column=1, sticky="nw", pady=2)
        if record.get("error"):
            ttk.Label(outer, text="Parsing or archive error: " + record["error"], foreground="#9c3500",
                      wraplength=720).pack(anchor="w", pady=(0, 6))
        ttk.Label(outer, text="Measurements preserved from XML", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(6, 4))
        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)
        columns = ("code", "label", "imperial", "imperial_units", "metric", "metric_units", "data_type", "other")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self.measurement_tree = tree
        headings = (("code", "Code", 85), ("label", "Label", 200), ("imperial", "Imperial", 100),
                    ("imperial_units", "Units", 72), ("metric", "Metric", 100),
                    ("metric_units", "Units", 72), ("data_type", "Type", 90), ("other", "Other XML fields", 220))
        for column, title, width in headings:
            tree.heading(column, text=title)
            tree.column(column, width=width, minwidth=50, stretch=column == "label")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(outer, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        xscroll.pack(fill="x")
        for field in record.get("measurements", []):
            known = ("Code", "Label", "ImperialValue", "ImperialUnits", "MetricValue", "MetricUnits", "DataType")
            other = {key: value for key, value in field.items() if key not in known}
            tree.insert("", "end", values=tuple(field.get(key, field.get("_text", "")) for key in known) +
                        (json.dumps(other, ensure_ascii=False) if other else "",))


class SettingsWindow:
    """Advanced controls and diagnostic detail separated from the main view."""
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title("Simulator Monitor Settings & Diagnostics")
        self.window.geometry("980x760")
        self.window.minsize(820, 600)
        self.window.configure(bg="#E5E5E5")
        self._build()
        self.update_health()
        self.refresh_results()
        self.refresh_notices()

    def _build(self):
        outer = ttk.Frame(self.window, padding=16, style="BTW.TFrame")
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Settings & Diagnostics", style="BTW.SectionTitle.TLabel").pack(anchor="w")

        dirs = ttk.LabelFrame(outer, text="Local directories", padding=10)
        dirs.pack(fill="x", pady=(10, 8))
        self.source_text = tk.StringVar(value=str(self.app.source_directory))
        self.archive_text = tk.StringVar(value=str(self.app.archive_directory))
        self.database_text = tk.StringVar(value=str(self.app.database_path))
        for row, (label, variable) in enumerate((("XML source", self.source_text),
                                                  ("Archive", self.archive_text),
                                                  ("Results database", self.database_text))):
            ttk.Label(dirs, text=label, style="BTW.FieldLabel.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=3)
            ttk.Label(dirs, textvariable=variable, wraplength=800).grid(row=row, column=1, sticky="nw", pady=3)
        dirs.columnconfigure(1, weight=1)

        health = ttk.LabelFrame(outer, text="Monitoring health", padding=10)
        health.pack(fill="x", pady=(0, 8))
        self.health_state = tk.StringVar()
        self.worker_state = tk.StringVar()
        self.source_state = tk.StringVar()
        self.last_scan_state = tk.StringVar()
        self.last_archive_state = tk.StringVar()
        for row, (label, variable) in enumerate((("Status", self.health_state),
                                                  ("Worker", self.worker_state),
                                                  ("Source accessible", self.source_state),
                                                  ("Last successful scan", self.last_scan_state),
                                                  ("Last XML archived", self.last_archive_state))):
            ttk.Label(health, text=label, style="BTW.FieldLabel.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14), pady=2)
            ttk.Label(health, textvariable=variable).grid(row=row, column=1, sticky="w", pady=2)
        actions = ttk.Frame(health, style="BTW.TFrame")
        actions.grid(row=0, column=2, rowspan=3, sticky="e", padx=(20, 0))
        self.start_button = ttk.Button(actions, text="Start Monitoring", style="BTW.TButton", command=self.app.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop Monitoring", style="BTW.TButton", command=self.app.stop)
        self.stop_button.pack(side="left", padx=(7, 0))
        health.columnconfigure(1, weight=1)

        notices = ttk.LabelFrame(outer, text="Recent warnings and errors", padding=8)
        notices.pack(fill="x", pady=(0, 8))
        self.notice_tree = ttk.Treeview(notices, columns=("time", "level", "message"), show="headings", height=4,
                                        style="BTW.Treeview")
        for key, label, width in (("time", "Time", 145), ("level", "Level", 90), ("message", "Details", 660)):
            self.notice_tree.heading(key, text=label)
            self.notice_tree.column(key, width=width, minwidth=65, stretch=key == "message", anchor="w")
        self.notice_tree.pack(fill="x")

        results = ttk.LabelFrame(outer, text="Recent results", padding=8)
        results.pack(fill="both", expand=True)
        columns = ("time", "ticket", "exercise", "score", "status")
        self.results_tree = ttk.Treeview(results, columns=columns, show="headings", height=8, style="BTW.Treeview")
        for key, label, width in (("time", "Time", 150), ("ticket", "Ticket", 135), ("exercise", "Exercise", 230),
                                  ("score", "Score", 140), ("status", "Archive status", 180)):
            self.results_tree.heading(key, text=label)
            self.results_tree.column(key, width=width, minwidth=70, stretch=key in {"exercise", "status"}, anchor="w")
        yscroll = ttk.Scrollbar(results, orient="vertical", command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=yscroll.set)
        self.results_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.results_tree.bind("<Double-1>", lambda _event: self.view_selected_result())
        self.results_tree.bind("<Return>", lambda _event: self.view_selected_result())

        footer = ttk.Frame(outer, style="BTW.TFrame")
        footer.pack(fill="x", pady=(9, 0))
        ttk.Button(footer, text="Refresh Results", style="BTW.TButton", command=self.refresh).pack(side="left")
        ttk.Button(footer, text="View Selected Result", style="BTW.TButton", command=self.view_selected_result).pack(side="left", padx=(7, 0))
        ttk.Button(footer, text="Open Archive Folder", style="BTW.TButton", command=self.app.open_archive_folder).pack(side="left", padx=(7, 0))
        ttk.Button(footer, text="Legacy OCR Diagnostics", style="BTW.TButton", command=self.app.open_legacy_diagnostics).pack(side="right")

    def update_health(self):
        app = self.app
        self.health_state.set(app.monitor_state)
        self.worker_state.set("Running" if app.worker and app.worker.is_alive() else "Stopped")
        self.source_state.set({None: "Not checked", True: "Available", False: "Unavailable"}[app.source_accessible])
        self.last_scan_state.set(_display_time(app.last_scan))
        self.last_archive_state.set(_display_time(app.last_capture))
        self.start_button.configure(state="disabled" if app.worker and app.worker.is_alive() else "normal")
        self.stop_button.configure(state="normal" if app.worker and app.worker.is_alive() else "disabled")

    def refresh_notices(self):
        for item in self.notice_tree.get_children():
            self.notice_tree.delete(item)
        for entry in reversed(self.app.recent_notices):
            self.notice_tree.insert("", "end", values=(_display_time(entry["time"]), entry["level"], entry["message"]))

    def refresh_results(self):
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        for index, record in enumerate(self.app.repository.recent(200)):
            state = "Archived / Parsed" if record["archive_status"] == "archived" and record["validation_status"] == "parsed" else "Needs review" if record["validation_status"] != "parsed" else "Archive pending"
            self.results_tree.insert("", "end", iid=record["result_id"], values=(
                _display_result_time(record["imported_at"]), _masked_ticket(record.get("ticket")),
                record.get("exercise") or "Unidentified", "Pending Formula", state),
                tags=("stripe_even" if index % 2 == 0 else "stripe_odd",))

    def refresh(self):
        self.app.refresh()
        self.update_health()
        self.refresh_notices()
        self.refresh_results()

    def view_selected_result(self):
        selection = self.results_tree.selection()
        if selection:
            return self.app.open_result_details(selection[0])
        return None


class XMLMonitorApp:
    """Simple main results view with operational controls in Settings."""
    RESULT_COUNTS = ("10", "25", "50", "100")
    COLORS = {
        "Starting": ("● STARTING", "#805b00", "#fff3cd"),
        "Active": ("● MONITORING ACTIVE", "#176b45", "#e8f4ed"),
        "Attention": ("● ACTIVE — ATTENTION", "#805b00", "#fff3cd"),
        "Unavailable": ("● SOURCE UNAVAILABLE", "#805b00", "#fff3cd"),
        "Stopped": ("● MONITORING STOPPED", "#8a3434", "#fbeaea"),
        "Stopping": ("● STOPPING", "#805b00", "#fff3cd"),
        "Error": ("● MONITOR ERROR", "#a12626", "#fbeaea"),
    }

    def __init__(self, root: tk.Tk, *, config_path: Path = CONFIG_PATH,
                 worker_factory=XMLMonitorWorker, repository_factory=XMLResultRepository,
                 autostart: bool = True, preferences_path: Path | None = None):
        self.root, self.config_path = root, Path(config_path)
        self.preferences_path = Path(preferences_path) if preferences_path else APP_DIR / "ui_preferences.json"
        self.result_count = self._load_result_count()
        self.worker_factory, self.repository_factory = worker_factory, repository_factory
        self.events: queue.Queue = queue.Queue()
        self.worker = None
        self.closing = False
        self.monitor_state = "Starting" if autostart else "Stopped"
        self.source_accessible = None
        self.last_scan = None
        self.last_capture = None
        self.recent_notices = []
        self._notice_dedupe = {}
        self.settings_window = None
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        btw_root = Path.home() / "Documents" / "BTW"
        self.source_directory = Path(config.get("xml_source_directory") or
                                     (Path.home() / "Documents" / "My Games" / "SimUCampusData" / "ExperienceMode"))
        self.archive_directory = Path(config.get("xml_archive_directory") or (btw_root / "simulator_xml_archive"))
        self.database_path = Path(config.get("xml_database_path") or (self.archive_directory / "results.sqlite3"))
        self.poll_interval = float(config.get("xml_poll_interval", 1.0))
        self.repository = repository_factory(self.database_path, self.archive_directory)
        self.last_scan = self.repository.last_successful_scan()
        self.last_capture = self.repository.last_imported_at()
        self.logger = logging.getLogger("btw.simulator_monitor")
        self._build()
        self._refresh_results()
        self._poll_events()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if autostart:
            self.root.after(250, self.start)

    def _build(self):
        self.root.title("BTW Simulator Results")
        self.root.geometry("1040x700")
        self.root.minsize(820, 560)
        self.root.configure(bg="#E5E5E5")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("BTW.TFrame", background="#E5E5E5")
        style.configure("BTW.SectionTitle.TLabel", font=("Segoe UI", 14, "bold"), foreground="#111111", background="#E5E5E5")
        style.configure("BTW.FieldLabel.TLabel", font=("Segoe UI", 9, "bold"), foreground="#111111", background="#E5E5E5")
        style.configure("BTW.TButton", font=("Segoe UI", 9, "bold"), padding=(11, 6),
                        background="#C8102E", foreground="#ffffff")
        style.map("BTW.TButton", background=[("active", "#A50D25")], foreground=[("disabled", "#777777")])
        style.configure("BTW.Header.TButton", font=("Segoe UI", 9, "bold"), padding=(10, 6),
                        background="#C8102E", foreground="#ffffff")
        style.map("BTW.Header.TButton", background=[("active", "#A50D25")])
        style.configure("BTW.Treeview", font=("Segoe UI", 10), rowheight=34,
                        background="#ffffff", fieldbackground="#ffffff", foreground="#111111")
        style.configure("BTW.Treeview.Heading", font=("Segoe UI", 9, "bold"),
                        background="#333333", foreground="#ffffff", padding=(8, 8))
        style.map("BTW.Treeview", background=[("selected", "#D0D0D0")], foreground=[("selected", "#111111")])
        style.configure("BTW.TLabelframe", background="#E5E5E5", foreground="#111111")
        style.configure("BTW.TLabelframe.Label", background="#E5E5E5", foreground="#111111")
        outer = ttk.Frame(self.root, padding=18, style="BTW.TFrame")
        outer.pack(fill="both", expand=True)
        outer.pack_propagate(False)

        header = tk.Frame(outer, bg="#000000", padx=20, pady=16)
        header.pack(fill="x")
        title_block = tk.Frame(header, bg="#000000")
        title_block.pack(side="left", fill="y")
        tk.Label(title_block, text="BUILT TO WORK", bg="#000000", fg="#ffffff",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(title_block, text="Simulator Results", bg="#000000", fg="#ffffff",
                 font=("Segoe UI", 21, "bold")).pack(anchor="w", pady=(2, 0))
        header_actions = tk.Frame(header, bg="#000000")
        header_actions.pack(side="right", fill="y")
        self.status_indicator = tk.Label(header_actions, font=("Segoe UI", 9, "bold"),
                                         padx=12, pady=8, relief="flat")
        self.status_indicator.pack(side="left", padx=(0, 10), pady=5)
        ttk.Button(header_actions, text="Settings", style="BTW.Header.TButton",
                   command=self.open_settings).pack(side="left", pady=5)
        self._set_monitor_state(self.monitor_state)

        results_header = ttk.Frame(outer, style="BTW.TFrame")
        results_header.pack(fill="x", pady=(22, 10))
        ttk.Label(results_header, text="Recent Results", style="BTW.SectionTitle.TLabel").pack(side="left")
        self.result_count_var = tk.StringVar(value=str(self.result_count))
        ttk.Label(results_header, text="Show", foreground="#111111", background="#E5E5E5").pack(side="right", padx=(10, 5))
        self.result_count_combo = ttk.Combobox(results_header, textvariable=self.result_count_var,
                                               values=self.RESULT_COUNTS, state="readonly", width=5)
        self.result_count_combo.pack(side="right")
        self.result_count_combo.bind("<<ComboboxSelected>>", self._on_result_count_changed)
        self.result_count_summary = ttk.Label(results_header, text="", foreground="#111111", background="#E5E5E5")
        self.result_count_summary.pack(side="right", padx=(0, 10))
        result_frame = ttk.Frame(outer, style="BTW.TFrame")
        result_frame.pack(fill="both", expand=True)
        self.results_tree = ttk.Treeview(result_frame, columns=("time", "ticket", "exercise", "score"),
                                         show="headings", height=12, style="BTW.Treeview")
        for key, title, width in (("time", "Time", 180), ("ticket", "Ticket", 175),
                                  ("exercise", "Exercise", 350), ("score", "Score", 190)):
            self.results_tree.heading(key, text=title)
            self.results_tree.column(key, width=width, minwidth=90, stretch=key == "exercise", anchor="w")
        yscroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=yscroll.set)
        self.results_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.results_tree.tag_configure("stripe_even", background="#FFFFFF", foreground="#111111")
        self.results_tree.tag_configure("stripe_odd", background="#F2F2F2", foreground="#111111")
        self.results_tree.bind("<Double-1>", lambda _event: self._show_selected())
        self.results_tree.bind("<Return>", lambda _event: self._show_selected())

    def _set_monitor_state(self, state: str):
        self.monitor_state = state if state in self.COLORS else "Error"
        text, foreground, background = self.COLORS[self.monitor_state]
        if hasattr(self, "status_indicator"):
            self.status_indicator.configure(text=text, fg=foreground, bg=background)
        if self.settings_window and self.settings_window.window.winfo_exists():
            self.settings_window.update_health()

    def _add_notice(self, level: str, message: str):
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        key = (level, message)
        previous = self._notice_dedupe.get(key)
        if previous is not None and time.monotonic() - previous < 60:
            return
        self._notice_dedupe[key] = time.monotonic()
        self.recent_notices.append({"time": now, "level": level, "message": message})
        self.recent_notices = self.recent_notices[-30:]
        if self.settings_window and self.settings_window.window.winfo_exists():
            self.settings_window.refresh_notices()

    def _refresh_results(self):
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        self.result_count_summary.configure(text=f"Showing {self.result_count} newest")
        for index, record in enumerate(self.repository.recent(self.result_count)):
            self.results_tree.insert("", "end", iid=record["result_id"], values=(
                _display_result_time(record["imported_at"]), _masked_ticket(record.get("ticket")),
                record.get("exercise") or "Unidentified", "Pending Formula"),
                tags=("stripe_even" if index % 2 == 0 else "stripe_odd",))
        if self.settings_window and self.settings_window.window.winfo_exists():
            self.settings_window.refresh_results()

    def _load_result_count(self):
        try:
            value = str(json.loads(self.preferences_path.read_text(encoding="utf-8")).get("result_count", "10"))
            return int(value) if value in self.RESULT_COUNTS else 10
        except (OSError, ValueError, TypeError, AttributeError):
            return 10

    def _on_result_count_changed(self, _event=None):
        value = self.result_count_var.get()
        if value not in self.RESULT_COUNTS:
            self.result_count_var.set(str(self.result_count))
            return
        self.result_count = int(value)
        try:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            self.preferences_path.write_text(json.dumps({"result_count": value}, indent=2) + "\n",
                                             encoding="utf-8")
        except OSError as exc:
            self.logger.warning("Could not save results display preference: %s", exc)
        self._refresh_results()

    def refresh(self):
        self._refresh_results()
        if self.worker and self.worker.is_alive():
            self.worker.request_scan()
        if self.settings_window and self.settings_window.window.winfo_exists():
            self.settings_window.update_health()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            self.repository.recover_pending_archives()
            self._refresh_results()
            self.source_accessible = None
            self._set_monitor_state("Starting")
            self.worker = self.worker_factory(self.source_directory, self.repository, self.events,
                                              poll_interval=self.poll_interval)
            self.worker.start()
            if self.settings_window and self.settings_window.window.winfo_exists():
                self.settings_window.update_health()
        except Exception as exc:
            self._set_monitor_state("Error")
            self._add_notice("Error", f"Could not start XML monitoring: {type(exc).__name__}: {exc}")
            self.logger.exception("Could not start XML monitoring")

    def stop(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self._set_monitor_state("Stopping")

    def open_settings(self):
        if self.settings_window and self.settings_window.window.winfo_exists():
            self.settings_window.window.deiconify()
            self.settings_window.window.lift()
            self.settings_window.update_health()
            return self.settings_window
        self.settings_window = SettingsWindow(self)
        return self.settings_window

    def open_archive_folder(self):
        try:
            if os.name == "nt":
                os.startfile(str(self.archive_directory))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(self.archive_directory)])
        except Exception as exc:
            messagebox.showerror("Archive folder", f"Could not open the local archive folder:\n{exc}", parent=self.root)

    def open_legacy_diagnostics(self):
        window = tk.Toplevel(self.root)
        try:
            MonitorApp(window, config_path=self.config_path, history_path=HISTORY_PATH)
        except Exception as exc:
            window.destroy()
            messagebox.showerror("Legacy OCR Diagnostics", f"Could not open legacy diagnostics:\n{exc}", parent=self.root)

    def open_result_details(self, result_id: str):
        record = self.repository.get(result_id)
        if record:
            return XMLResultDetailsDialog(self.root, record)
        return None

    def _show_selected(self, _event=None):
        selection = self.results_tree.selection()
        if selection:
            self.open_result_details(selection[0])

    def _handle_event(self, kind: str, data: dict):
        if kind == "xml_monitor_started":
            if self.worker and self.worker.is_alive():
                self._set_monitor_state("Starting")
        elif kind == "xml_scan":
            if data.get("directory_exists"):
                recovered = self.source_accessible is False
                self.source_accessible = True
                self.last_scan = data.get("scanned_at")
                if data.get("errors"):
                    self._set_monitor_state("Attention")
                    for error in data["errors"]:
                        self._add_notice("Warning", error)
                elif self.worker and self.worker.is_alive() and self.monitor_state != "Stopping":
                    self._set_monitor_state("Active")
                if recovered:
                    self._add_notice("Info", "Simulator XML source is available again.")
            else:
                self.source_accessible = False
                self._set_monitor_state("Unavailable")
                self._add_notice("Warning", data.get("error") or "Simulator XML source directory is unavailable.")
            if self.settings_window and self.settings_window.window.winfo_exists():
                self.settings_window.update_health()
        elif kind == "xml_result":
            record = data["record"]
            if record.get("validation_status") != "parsed":
                self._add_notice("Warning", f"Result needs review: {record.get('source_filename', 'scorecard.xml')}")
            self._refresh_results()
        elif kind == "xml_monitor_error":
            self._set_monitor_state("Error")
            message = data.get("error", "XML monitor error")
            self._add_notice("Error", message)
            self.logger.error(message)
        elif kind == "xml_monitor_stopped":
            if self.monitor_state != "Error":
                self._set_monitor_state("Stopped")
            if self.settings_window and self.settings_window.window.winfo_exists():
                self.settings_window.update_health()

    def _poll_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle_event(kind, data)
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(200, self._poll_events)

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self._wait_for_worker_close()
        else:
            self.root.destroy()

    def _wait_for_worker_close(self):
        if self.worker and self.worker.is_alive():
            self.root.after(100, self._wait_for_worker_close)
        else:
            self.root.destroy()


def _single_instance():
    if os.name != "nt":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, "Local\\BTWSimulatorMonitor")
    if not handle:
        return True
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    # Keep the handle alive for the process lifetime.
    globals()["_MUTEX_HANDLE"] = handle
    return True


def main():
    if not _single_instance():
        try:
            tk.Tk().withdraw()
            messagebox.showinfo("BTW Simulator Monitor", "BTW Simulator Monitor is already open.")
        except tk.TclError:
            pass
        return 0
    try:
        logger = logging.getLogger("btw.simulator_monitor")
        logger.setLevel(logging.INFO)
        logger.addHandler(RotatingFileHandler(APP_DIR / "monitor.log", maxBytes=1_000_000,
                                              backupCount=2, encoding="utf-8"))
        root = tk.Tk()
        XMLMonitorApp(root)
        root.mainloop()
    except Exception as exc:
        logging.getLogger("btw.simulator_monitor").exception("Monitor startup or UI failure")
        try:
            tk.Tk().withdraw()
            messagebox.showerror("BTW Simulator Monitor", f"The monitor could not open:\n{type(exc).__name__}: {exc}")
        except tk.TclError:
            logging.getLogger("btw.monitor").exception("GUI startup failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
