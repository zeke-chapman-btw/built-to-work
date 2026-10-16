from pathlib import Path
from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateParticipantDocumentStorage(FileSystemStorage):
    """Private local storage; files are served only by permission-checked views."""

    def __init__(self, *args, **kwargs):
        root = getattr(settings, "PRIVATE_DOCUMENT_ROOT", None)
        if root is None:
            root = Path(settings.PRIVATE_DOCUMENT_ROOT)
        kwargs.setdefault("location", root)
        kwargs.setdefault("file_permissions_mode", 0o600)
        kwargs.setdefault("directory_permissions_mode", 0o700)
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)

    def url(self, name):
        raise ValueError("Private participant documents have no public URL.")
