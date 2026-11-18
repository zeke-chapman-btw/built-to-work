"""Authenticator-app codes (RFC 6238, 30 seconds, 6 digits)."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    padded = secret + "=" * (-len(secret) % 8)
    return base64.b32decode(padded.upper())


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def current_step(now=None):
    return int((now if now is not None else time.time()) // 30)


def verify(secret, code, *, after_step=0, now=None, window=1):
    """The matching time step (to remember), or None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    step = current_step(now)
    for s in range(step - window, step + window + 1):
        if s > after_step and hmac.compare_digest(code_at(secret, s), code):
            return s
    return None


def uri(secret, account, issuer="Built to Work"):
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def qr_data_uri(text):
    import io
    import qrcode
    image = qrcode.make(text, box_size=6, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
