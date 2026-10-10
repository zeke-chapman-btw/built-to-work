import json
import os
import time
from urllib import request, error
from django.core.management.base import BaseCommand
from apps.events.models import SyncOutboxItem
from apps.events.sync import acknowledge_sync, build_sync_payload, claim_sync_item, record_sync_failure


class Command(BaseCommand):
    help = "Deliver pending trailer synchronization envelopes"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--endpoint", default=os.environ.get("BTW_SYNC_ENDPOINT", ""))

    def handle(self, *args, **options):
        endpoint = options["endpoint"]
        if not endpoint:
            self.stderr.write("BTW_SYNC_ENDPOINT is required")
            return
        while True:
            item = SyncOutboxItem.objects.filter(status__in=[SyncOutboxItem.Status.PENDING, SyncOutboxItem.Status.FAILED]).order_by("created_at").first()
            if item is None:
                if options["once"]:
                    return
                time.sleep(5)
                continue
            claimed = claim_sync_item(item.pk)
            if claimed is None:
                continue
            try:
                body = json.dumps(build_sync_payload(claimed)).encode("utf-8")
                req = request.Request(endpoint, data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('BTW_SYNC_API_TOKEN', '')}"})
                with request.urlopen(req, timeout=15) as response:
                    if response.status not in (200, 202):
                        raise RuntimeError(f"central returned HTTP {response.status}")
                acknowledge_sync(claimed.pk)
                self.stdout.write(f"sent {claimed.operation_key}")
            except (OSError, error.HTTPError, RuntimeError, ValueError) as exc:
                record_sync_failure(claimed.pk, exc)
                self.stderr.write(f"retry scheduled for {claimed.operation_key}: {exc}")
            if options["once"]:
                return
