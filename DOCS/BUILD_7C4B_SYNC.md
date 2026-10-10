# Build 7C-4B Central Synchronization Foundation

This checkpoint adds the first authenticated central-ingest boundary for trailer offline-registration envelopes. Trailer operations remain durable in the existing SyncOutboxItem queue and can be claimed, retried with bounded exponential backoff, and acknowledged idempotently.

## Contract

POST JSON to /events/sync/ingest/ with a bearer token configured by BTW_SYNC_API_TOKEN. Each envelope carries an operation key, stable trailer identity, event and preparation scope, ticket number, participant UUID, consent version/hash, group, and custom answers. Duplicate operation keys return the original receipt without re-importing.

Central receipts are durable and preserve accepted versus review status. Unknown trailer/event scopes are rejected. Participant identities that cannot be reconciled exactly are retained for review and do not invalidate the participant ticket.

Staff can inspect aggregate queue/receipt status at /events/sync/status/.

## Operational limitations

The receiving API and outbox helpers are implemented in this repository, but a separate trailer and central deployment have not yet been exercised together. Per-trailer credential provisioning, a production worker/scheduler, and full participant/registration import reconciliation remain 7C-4B follow-up work. The current API deliberately fails closed when BTW_SYNC_API_TOKEN is unset.

Build 7C-4C will cover recovery communications and operator-facing conflict resolution.
