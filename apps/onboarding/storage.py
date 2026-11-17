from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateOnboardingStorage(FileSystemStorage):
    """W-9s live outside the public media folder and are only served to staff."""
    def __init__(self):
        super().__init__(location=None, base_url=None, file_permissions_mode=0o600, directory_permissions_mode=0o700)

    @property
    def location(self):
        root = getattr(settings, "PRIVATE_ONBOARDING_ROOT", None) or (Path(settings.MEDIA_ROOT).parent / "private_onboarding")
        return str(Path(root).resolve())

    @property
    def base_location(self):
        return self.location

    def deconstruct(self):
        return ("apps.onboarding.storage.PrivateOnboardingStorage", [], {})
