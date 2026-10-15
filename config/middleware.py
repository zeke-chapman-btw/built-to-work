import base64
import binascii
import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse


class PreviewAccessMiddleware:
    """HTTP Basic gate for an explicitly enabled non-production preview."""

    health_path = "/health/"

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "BTW_PREVIEW_GATE_ENABLED", False)
        self.username = getattr(settings, "BTW_PREVIEW_GATE_USERNAME", "")
        self.password = getattr(settings, "BTW_PREVIEW_GATE_PASSWORD", "")
        if self.enabled and (not self.username or not self.password or ":" in self.username):
            raise ImproperlyConfigured(
                "The preview access gate requires a username and password; username cannot contain ':'"
            )

    def __call__(self, request):
        if not self.enabled or request.path_info == self.health_path:
            return self.get_response(request)
        if not self._authorized(request):
            response = HttpResponse("Preview access required.", status=401, content_type="text/plain")
            response["WWW-Authenticate"] = 'Basic realm="BTW development preview", charset="UTF-8"'
            response["Cache-Control"] = "no-store"
            return response
        return self.get_response(request)

    def _authorized(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        scheme, separator, token = header.partition(" ")
        if not separator or scheme.lower() != "basic":
            return False
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return False
        username_matches = secrets.compare_digest(username.encode(), self.username.encode())
        password_matches = secrets.compare_digest(password.encode(), self.password.encode())
        return username_matches and password_matches
