import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env", override=False)

from .base import *  # noqa: E402,F403

DEBUG = os.environ.get("DEBUG", "True").lower() in {"1", "true", "yes"}
SECRET_KEY = os.environ.get(
    "SECRET_KEY", "django-insecure-development-only-do-not-use-in-production"
)

_default_hosts = ["localhost", "127.0.0.1", "[::1]"]
_codespace_name = os.environ.get("CODESPACE_NAME")
_codespace_domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
if _codespace_name and _codespace_domain:
    _default_hosts.append(f"{_codespace_name}-8000.{_codespace_domain}")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", ",".join(_default_hosts)).split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]