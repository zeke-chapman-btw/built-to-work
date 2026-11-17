"""A small Stripe client (form-encoded HTTPS). Keys come from Settings → Billing."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib import error, parse, request

API = "https://api.stripe.com/v1/"
PURPOSE = "stripe-secret"


class StripeError(Exception):
    pass


def _crypto():
    from apps.messaging import crypto
    return crypto


def secret_key():
    from .models import StripeSettings
    row = StripeSettings.current()
    return _crypto().decrypt(row.secret_key_encrypted, PURPOSE) if row.secret_key_encrypted else ""


def webhook_secret():
    from .models import StripeSettings
    row = StripeSettings.current()
    return _crypto().decrypt(row.webhook_secret_encrypted, PURPOSE) if row.webhook_secret_encrypted else ""


def encrypt(value):
    return _crypto().encrypt(value, PURPOSE) if value else ""


def configured():
    try:
        return bool(secret_key())
    except Exception:
        return False


def test_mode():
    key = secret_key()
    return key.startswith(("sk_test_", "rk_test_"))


def flatten(data, prefix=""):
    pairs = []
    for key, value in (data or {}).items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            pairs += flatten(value, name)
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    pairs += flatten(item, f"{name}[{i}]")
                else:
                    pairs.append((f"{name}[{i}]", str(item)))
        elif isinstance(value, bool):
            pairs.append((name, "true" if value else "false"))
        elif value is not None:
            pairs.append((name, str(value)))
    return pairs


def call(method, path, data=None, *, idempotency_key=None, key=None):
    key = key or secret_key()
    if not key:
        raise StripeError("Stripe isn't set up yet (Settings → Billing).")
    body = parse.urlencode(flatten(data)).encode() if data and method == "POST" else None
    url = API + path + (("?" + parse.urlencode(flatten(data))) if data and method == "GET" else "")
    headers = {"Authorization": f"Bearer {key}", "Stripe-Version": "2024-06-20"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("error", {})
        except ValueError:
            detail = {}
        raise StripeError(detail.get("message") or f"Stripe error {exc.code}") from None
    except (error.URLError, TimeoutError):
        raise StripeError("Stripe could not be reached.") from None


def verify(payload, header, secret, tolerance=300):
    """Check the Stripe-Signature header (t=…,v1=…)."""
    if not (secret and header):
        return False
    parts = {}
    for piece in header.split(","):
        if "=" in piece:
            k, v = piece.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())
    try:
        stamp = int(parts.get("t", ["0"])[0])
    except ValueError:
        return False
    if abs(time.time() - stamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))
