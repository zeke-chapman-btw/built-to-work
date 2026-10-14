"""Private storage for original historical import spreadsheets."""
from pathlib import Path
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

@deconstructible
class PrivateHistoricalStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=None, base_url=None, file_permissions_mode=0o600, directory_permissions_mode=0o700)

    @property
    def location(self):
        return str(Path(settings.PRIVATE_HISTORICAL_ROOT).resolve())

    @property
    def base_url(self):
        return None
