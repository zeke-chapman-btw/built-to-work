"""Wording for the sign-up welcome and finish screens.

Each form version can override any of these; blanks fall back to the defaults below,
which match what participants saw before the wording became editable.
"""

DEFAULTS = {
    "welcome_eyebrow": "Your next chapter starts here",
    "welcome_title": "Put your skills to work.",
    "welcome_message": ("At Built to Work, we’re powering the American workforce. Our goal is to help you find "
                        "the right opportunities to put your skills to work."),
    "welcome_challenge": "Skilled trades deserve skilled people. Show us what you’ve got.",
    "welcome_button": "Start intake",
    "welcome_details": "5 steps · About 10 minutes · Ages 14 and up",
    "finish_title": "You're all set",
    "finish_message": "We've received your information. Welcome to Built to Work!",
}

LABELS = {
    "welcome_eyebrow": "Small line above the title",
    "welcome_title": "Welcome title",
    "welcome_message": "Welcome message",
    "welcome_challenge": "Second line",
    "welcome_button": "Start button",
    "welcome_details": "Small print under the button",
    "finish_title": "Finish title",
    "finish_message": "Finish message",
}

OPTIONAL = {"welcome_challenge"}

LIMITS = {key: (600 if key.endswith("message") else 160) for key in DEFAULTS}


def clean_screens(values):
    cleaned = {}
    for key in DEFAULTS:
        text = str((values or {}).get(key) or "").strip()[:LIMITS[key]]
        if text and text != DEFAULTS[key]:
            cleaned[key] = text
        elif key in OPTIONAL and key in (values or {}) and not text:
            cleaned[key] = ""  # deliberately hidden
    return cleaned


def public_screens(version):
    saved = getattr(version, "screens", None) or {}
    return {key: (saved[key] if key in saved else default) for key, default in DEFAULTS.items()}
