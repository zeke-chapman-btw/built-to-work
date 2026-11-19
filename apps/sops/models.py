"""Standard operating procedures staff can read, managed by administrators."""
import uuid

from django.conf import settings
from django.db import models


class SOP(models.Model):
    class Kind(models.TextChoices):
        PAGE = "page", "Written page"
        PDF = "pdf", "PDF file"
        DOC = "doc", "Google Drive"

    class Audience(models.TextChoices):
        ALL = "all", "All staff"
        EVENT_STAFF = "event_staff", "Event staff"
        RECRUITER = "recruiter", "Recruiters"
        MANAGER = "manager", "Managers and admins only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=160)
    category = models.CharField(max_length=80, blank=True, help_text="Groups SOPs on the list, for example Trailer setup.")
    summary = models.CharField(max_length=240, blank=True, help_text="One line shown on the list.")
    kind = models.CharField("Type", max_length=8, choices=Kind.choices, default=Kind.PAGE)
    body = models.TextField(blank=True, help_text="Start a line with # for a heading, - for a bullet or 1. for a numbered step.")
    doc_url = models.URLField("Google Drive link", max_length=500, blank=True,
                              help_text="Paste the link to a Google Doc or a file in Google Drive (like a PDF). Share it as \"Anyone with the link · Viewer\". Changes made in Drive show here right away.")
    pdf_key = models.CharField(max_length=255, blank=True, editable=False)
    pdf_name = models.CharField(max_length=255, blank=True, editable=False)
    pdf_size = models.PositiveIntegerField(default=0, editable=False)
    audience = models.CharField("Who can see it", max_length=16, choices=Audience.choices, default=Audience.ALL)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position", "title")
        permissions = [("manage_sops", "Can manage the SOP library")]

    @property
    def doc_preview_url(self):
        found = drive_link(self.doc_url)
        return found[1] if found else ""

    @property
    def doc_open_url(self):
        found = drive_link(self.doc_url)
        return found[2] if found else self.doc_url

    def __str__(self):
        return self.title


DOC_ID = __import__("re").compile(r"docs\.google\.com/(document|spreadsheets|presentation)/d/([A-Za-z0-9_-]{20,})")
FILE_ID = __import__("re").compile(r"drive\.google\.com/(?:file/d/|open\?id=)([A-Za-z0-9_-]{20,})")


def drive_link(url):
    """(id, preview link, open link) for a Google Doc, Sheet, Slides or a Drive file such as a PDF."""
    match = DOC_ID.search(url or "")
    if match:
        kind, found = match.groups()
        base = f"https://docs.google.com/{kind}/d/{found}"
        return found, f"{base}/preview", f"{base}/view" if kind == "document" else f"{base}/edit"
    match = FILE_ID.search(url or "")
    if match:
        base = f"https://drive.google.com/file/d/{match.group(1)}"
        return match.group(1), f"{base}/preview", f"{base}/view"
    return None


def doc_id(url):
    found = drive_link(url)
    return found[0] if found else ""
