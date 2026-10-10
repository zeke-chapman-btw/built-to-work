"""Private local storage for original consent PDFs."""
from pathlib import Path
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateConsentStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=None, base_url=None)

    @property
    def location(self):
        return str(Path(settings.PRIVATE_CONSENT_ROOT).resolve())

    @property
    def base_url(self):
        return None
