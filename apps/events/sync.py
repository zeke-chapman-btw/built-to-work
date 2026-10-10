import math
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import OfflineRegistrationEnvelope, SyncOutboxItem


def build_sync_payload(item):
    envelope = item.preparation.registrations.filter(payload__operation_key=item.operation_key).first()
    payload = dict(item.payload or {})
    if envelope:
        payload.setdefault("participant_identity", str(envelope.participant_identity))
        payload.setdefault("registration_id", str(envelope.registration_id) if envelope.registration_id else None)
        payload.setdefault("ticket_number", envelope.ticket_number)
        payload.setdefault("consent_document_version", envelope.consent_document_version)
        payload.setdefault("consent_hash", envelope.consent_hash)
        payload.setdefault("custom_answers", envelope.custom_answers)
    return {
        "operation_key": item.operation_key,
        "operation_type": item.operation_type,
        "trailer_identity": item.preparation.trailer.identity,
        "event_id": str(item.preparation.event_id),
        "preparation_id": str(item.preparation_id),
        "payload": payload,
    }


@transaction.atomic
def claim_sync_item(item_id):
    item = SyncOutboxItem.objects.select_for_update().select_related("preparation__trailer").get(pk=item_id)
    if item.status not in (SyncOutboxItem.Status.PENDING, SyncOutboxItem.Status.FAILED):
        return None
    if item.next_attempt_at and item.next_attempt_at > timezone.now():
        return None
    item.status = SyncOutboxItem.Status.IN_FLIGHT
    item.attempts += 1
    item.save(update_fields=["status", "attempts", "updated_at"])
    return item


@transaction.atomic
def record_sync_failure(item_id, error, *, max_delay_seconds=3600):
    item = SyncOutboxItem.objects.select_for_update().get(pk=item_id)
    delay = min(max_delay_seconds, 2 ** min(item.attempts, 10))
    item.status = SyncOutboxItem.Status.FAILED
    item.last_error = str(error)[:2000]
    item.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    item.save(update_fields=["status", "last_error", "next_attempt_at", "updated_at"])
    return item


@transaction.atomic
def acknowledge_sync(item_id, *, status=OfflineRegistrationEnvelope.Status.SYNCED, error=""):
    item = SyncOutboxItem.objects.select_for_update().get(pk=item_id)
    item.status = SyncOutboxItem.Status.SENT
    item.accepted_at = timezone.now()
    item.last_error = error
    item.save(update_fields=["status", "accepted_at", "last_error", "updated_at"])
    envelopes = OfflineRegistrationEnvelope.objects.filter(preparation=item.preparation, payload__operation_key=item.operation_key)
    envelopes.update(status=status, synced_at=timezone.now())
    return item
