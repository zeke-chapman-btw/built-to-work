import re

def normalize_email(value):
    return (value or "").strip().casefold()

def normalize_phone(value):
    return re.sub(r"\D", "", value or "")
