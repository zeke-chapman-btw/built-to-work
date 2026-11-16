"""Encryption for stored sign-ins and tokens, keyed from the deployment secret."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet(purpose):
    key = hashlib.sha256((f"btw-{purpose}:" + settings.SECRET_KEY).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value, purpose="secret"):
    return _fernet(purpose).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value, purpose="secret"):
    if not value:
        return ""
    try:
        return _fernet(purpose).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
