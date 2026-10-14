"""Pull prepared central events and public pre-registrations into this trailer."""
import json
import os
import time
from datetime import timedelta
from urllib import error, parse, request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.events.downsync import apply_download, when
from apps.events.models import CentralEventDownload, CentralEventDownloadPage


class Command(BaseCommand):
    help = "Download prepared events and pre-registrations from central BTW"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--force", action="store_true", help="Ignore retry delay for an operator-triggered run")
        parser.add_argument("--endpoint", default=os.environ.get("BTW_CENTRAL_DOWNLOAD_URL", ""))

    def handle(self, *args, **options):
        endpoint = options["endpoint"]
        identity = os.environ.get("BTW_TRAILER_IDENTITY", "").strip()
        token = os.environ.get("BTW_SYNC_API_TOKEN", "").strip()
        if not endpoint or not identity or not token:
            raise CommandError("Central URL, trailer identity, and trailer credential must be configured")
        parsed = parse.urlparse(endpoint)
        if parsed.scheme != "https" and not (settings.DEBUG and parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
            raise CommandError("Central event download requires HTTPS outside local development")
        while True:
            try:
                catalog = self.fetch(endpoint, identity, token, {})
                for item in catalog["events"]:
                    self.pull_event(endpoint, identity, token, item, force=options["force"])
            except (OSError, ValueError, KeyError, error.HTTPError) as exc:
                CentralEventDownload.objects.filter(trailer_identity=identity).update(last_error="Central catalog unavailable; retry scheduled")
                self.stderr.write(f"Central event catalog unavailable: {type(exc).__name__}")
            if options["once"]:
                return
            time.sleep(30)

    @staticmethod
    def fetch(endpoint, identity, token, params):
        url = endpoint + ("?" + parse.urlencode(params) if params else "")
        req = request.Request(url, headers={"Authorization": f"Bearer {token}",
                                            "X-Trailer-Identity": identity, "Accept": "application/json"})
        with request.urlopen(req, timeout=15) as response:
            if response.status != 200:
                raise ValueError("Central download refused")
            return json.loads(response.read())

    def pull_event(self, endpoint, identity, token, item, *, force=False):
        event_id = item["event_id"]
        prep_id = item["preparation_id"]
        download, _ = CentralEventDownload.objects.get_or_create(
            trailer_identity=identity, event_id=event_id,
            defaults={"preparation_id": prep_id})
        download.central_registration_count = item.get("central_registration_count")
        download.central_event_updated_at = when(item.get("central_event_updated_at"))
        download.last_catalog_at = timezone.now()
        if download.status == CentralEventDownload.Status.READY:
            download.last_error = ""
        download.save(update_fields=("central_registration_count", "central_event_updated_at", "last_catalog_at", "last_error", "updated_at"))
        if str(download.preparation_id) != prep_id:
            download.status = CentralEventDownload.Status.FAILED
            download.last_error = "Central preparation identity changed; staff review required"
            download.save(update_fields=("status", "last_error", "updated_at"))
            return
        if not force and download.next_attempt_at > timezone.now():
            return
        try:
            while True:
                params = {"event_id": event_id}
                if download.next_cursor:
                    params["cursor"] = download.next_cursor
                elif download.checkpoint_at:
                    params["since"] = download.checkpoint_at.isoformat()
                payload = self.fetch(endpoint, identity, token, params)
                if payload["event_id"] != event_id or payload["preparation_id"] != prep_id:
                    raise ValueError("Central response scope mismatch")
                with transaction.atomic():
                    download = CentralEventDownload.objects.select_for_update().get(pk=download.pk)
                    if not download.next_cursor:
                        download.pages.all().delete()
                        if not isinstance(payload.get("config"), dict):
                            raise ValueError("First page lacks event configuration")
                        download.config = payload["config"]
                        download.snapshot_at = when(payload["snapshot_at"])
                    elif payload["snapshot_at"] != download.snapshot_at.isoformat():
                        raise ValueError("Downloaded snapshot changed mid-transfer")
                    CentralEventDownloadPage.objects.create(download=download, page_number=download.pages.count(),
                                                            records=payload["records"])
                    download.next_cursor = payload["next_cursor"]
                    download.status = CentralEventDownload.Status.DOWNLOADING
                    download.last_error = ""
                    download.save(update_fields=("config", "snapshot_at", "next_cursor", "status", "last_error", "updated_at"))
                if not download.next_cursor:
                    count = apply_download(download)
                    download.attempts = 0
                    download.next_attempt_at = timezone.now()
                    download.save(update_fields=("attempts", "next_attempt_at", "updated_at"))
                    self.stdout.write(f"Prepared event {event_id}; {count} registration updates")
                    return
        except Exception as exc:
            # Keep staged pages and cursor across worker/process restarts.
            download.refresh_from_db()
            download.attempts += 1
            download.next_attempt_at = timezone.now() + timedelta(seconds=min(30 * 2 ** min(download.attempts, 7), 3600))
            download.status = CentralEventDownload.Status.FAILED
            download.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            download.save(update_fields=("attempts", "next_attempt_at", "status", "last_error", "updated_at"))
            self.stderr.write(f"Event {event_id} download pending retry: {type(exc).__name__}")
