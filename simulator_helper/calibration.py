"""Local, visual rectangle calibration. Never sends a participant capture."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .core import ConfigurationError, Rectangle, load_json
from .windows import WindowsCapture


def rectangle_from_drag(start: tuple[float, float], end: tuple[float, float],
                        scale_x: float, scale_y: float, width: int, height: int) -> Rectangle:
    x1, x2 = sorted((round(start[0] * scale_x), round(end[0] * scale_x)))
    y1, y2 = sorted((round(start[1] * scale_y), round(end[1] * scale_y)))
    return Rectangle.from_dict({"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}, width, height)


def save_region(profile_path: Path, profile: dict, region_name: str, rect: Rectangle, context: dict):
    if region_name not in ("identifier", "score", "state"):
        raise ConfigurationError("Unknown calibration region")
    updated = dict(profile)
    updated["expected_width"] = context["width"]
    updated["expected_height"] = context["height"]
    updated["display_context"] = context
    updated["profile_version"] = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    if region_name == "state":
        updated["state_regions"] = list(updated.get("state_regions", [])) + [rect.as_dict()]
    else:
        updated[region_name + "_region"] = rect.as_dict()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = profile_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(profile_path)
    return updated


def calibrate(config_path: Path, region_name: str):
    from PIL import ImageTk
    import tkinter as tk
    from tkinter import messagebox

    config = load_json(config_path)
    profile_path = (config_path.parent / config.get("profile_path", "profile.json")).resolve()
    profile = load_json(profile_path)
    if "profile_id" not in profile:
        raise ConfigurationError("Set an existing server profile_id in profile.json first")
    capture = WindowsCapture()
    try:
        screenshot = capture.full_desktop()
        context = capture.context
    finally:
        capture.close()
    root = tk.Tk()
    root.title("BTW Simulator Calibration - " + region_name.upper())
    screen_width = max(400, root.winfo_screenwidth() - 100)
    screen_height = max(300, root.winfo_screenheight() - 170)
    preview = screenshot.copy()
    preview.thumbnail((min(screen_width, 1800), min(screen_height, 1000)))
    scale_x = screenshot.width / preview.width
    scale_y = screenshot.height / preview.height
    photo = ImageTk.PhotoImage(preview)
    heading = tk.Label(root, text="Drag a box around " + region_name.upper() + " only. Save or retry.", font=("Segoe UI", 16))
    heading.pack(pady=6)
    canvas = tk.Canvas(root, width=preview.width, height=preview.height, highlightthickness=0, cursor="crosshair")
    canvas.pack()
    canvas.create_image(0, 0, image=photo, anchor="nw")
    details = tk.StringVar(value="No rectangle selected")
    tk.Label(root, textvariable=details, font=("Consolas", 12)).pack(pady=4)
    selection = {"start": None, "item": None, "rect": None}

    def start(event):
        if selection["item"]:
            canvas.delete(selection["item"])
        selection["start"] = (event.x, event.y)
        selection["rect"] = None
        selection["item"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                     outline="#d51e2c", width=3)

    def move(event):
        if selection["start"]:
            canvas.coords(selection["item"], *selection["start"], event.x, event.y)

    def finish(event):
        if not selection["start"]:
            return
        try:
            rect = rectangle_from_drag(selection["start"], (event.x, event.y),
                                       scale_x, scale_y, screenshot.width, screenshot.height)
            selection["rect"] = rect
            details.set(f"{region_name.upper()}: x={rect.x} y={rect.y} width={rect.width} height={rect.height}")
        except ConfigurationError as exc:
            details.set(str(exc))
        selection["start"] = None

    def retry():
        if selection["item"]:
            canvas.delete(selection["item"])
        selection.update(start=None, item=None, rect=None)
        details.set("No rectangle selected")

    def save():
        if selection["rect"] is None:
            messagebox.showerror("Calibration", "Select a rectangle first")
            return
        updated = save_region(profile_path, profile, region_name, selection["rect"], context)
        messagebox.showinfo('Saved', 'Saved {} to {}; profile version {}'.format(region_name, profile_path, updated['profile_version']))
        root.destroy()

    canvas.bind("<ButtonPress-1>", start)
    canvas.bind("<B1-Motion>", move)
    canvas.bind("<ButtonRelease-1>", finish)
    buttons = tk.Frame(root)
    buttons.pack(pady=6)
    tk.Button(buttons, text="Save", command=save, width=14).pack(side="left", padx=8)
    tk.Button(buttons, text="Retry", command=retry, width=14).pack(side="left", padx=8)
    tk.Button(buttons, text="Cancel", command=root.destroy, width=14).pack(side="left", padx=8)
    root.mainloop()
