"""Windows desktop region capture and fixed-region Tesseract recognition."""
from __future__ import annotations

import csv
import io
import os
import subprocess

from .core import ConfigurationError, Reading, Rectangle


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


def read_region(image, kind: str, *, tesseract_cmd: str | None = None) -> Reading:
    """OCR one cropped rectangle. Preserve the raw OCR text for the server."""
    from PIL import ImageOps
    image = ImageOps.autocontrast(ImageOps.grayscale(image))
    image = image.resize((image.width * 2, image.height * 2))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" if kind == "identifier" else "0123456789.%"
    command = [tesseract_cmd or os.environ.get("TESSERACT_CMD", "tesseract"), "stdin", "stdout",
               "--psm", "7", "-c", "tessedit_char_whitelist=" + whitelist, "tsv"]
    result = subprocess.run(command, input=stream.getvalue(), capture_output=True, timeout=8, check=False)
    if result.returncode:
        raise RuntimeError("Tesseract OCR failed; verify the executable and calibrated region")
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
