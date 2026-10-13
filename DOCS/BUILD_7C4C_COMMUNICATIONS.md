# Build 7C-4C communications and alerts (development review)

This checkpoint extends the existing durable trailer registration and ticket models. It does not change ticket allocation, QR payloads, consent approval, central synchronization, or the portable backup format.

## Participant ticket delivery

Public event registration and trailer offline registration each show initially unchecked **Email my event ticket** and **Text my event ticket** choices. A channel requires the corresponding contact detail. The registration stores the two choices and an opt-in timestamp. A ticket delivery is queued only after a current 10-digit ticket and accepted approved consent for the ticket event exists. Repeated queue requests reuse the same registration/channel/destination record.

Email includes the existing branded PNG ticket, whose QR renderer encodes `tel:` followed by the unchanged ten digits. SMS includes that ticket number; it does not issue a new ticket. Print/download remains available even if delivery fails. If contact details or opt-in change before send, delivery stops for staff review.

The `deliver_communications` command runs one pass by default. Run `python manage.py deliver_communications --loop --poll-seconds 30` under a supervised process on the trailer for automatic retry after connectivity returns. Pending and retryable failed rows persist in the database. Backoff doubles up to one hour, with six automatic attempts. A claimed send is marked **uncertain** until the provider acknowledges it; an interrupted worker or timeout therefore never silently triggers a duplicate send. Staff can review failed/uncertain rows and retry with a required reason and audit record. A provider that honors the supplied idempotency key can deduplicate SMS retries; SMTP cannot guarantee exactly-once delivery. A staff retry of an uncertain send can still duplicate a message and is explicitly labeled for review.

Configure the provider through environment variables; do not put credentials in Git:

- `BTW_TICKET_EMAIL_FROM` plus Django's `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `EMAIL_USE_TLS`.
- `BTW_SMS_PROVIDER_URL` and `BTW_SMS_PROVIDER_TOKEN`. The adapter requires HTTPS without embedded URL credentials. It sends JSON `{to, message, idempotency_key}` with a bearer token and `Idempotency-Key` header, expecting HTTP 200/201/202. A production SMS provider needs an adapter or endpoint that accepts this contract.

The automated tests use Django's in-memory email backend and mocked SMS/provider calls; no real messages are sent. Live deliverability, opt-out/STOP handling, sender registration, and provider webhook reconciliation remain deployment checks before production use.

## Operational notifications

Authorized administrators configure `OperationalNotificationRule` records in Django Admin. One global rule per incident kind is the default; an event rule for the same kind overrides it. Each rule selects email and/or SMS channels and recipients. Enabled rules require a valid recipient for each selected channel. Backup failures, synchronization failures, and exhausted offline ticket ranges create deduplicated open incidents. An incident queues one notification per matching recipient/channel; repeat observations increment its occurrence count without sending a duplicate. Resolved incidents may be reported again as new incidents. Rules may also be used for other critical operational conditions through `emit_incident`.

The same worker sends queued alerts while online and retries known transient failures. Incident and delivery history remain in the database. The staff operations dashboard shows open incidents, queued/failed alert deliveries, queued/failed/uncertain ticket deliveries, ticket ranges, and the last accepted sync. It independently performs a two-second live HTTP reachability check of `BTW_CENTRAL_SYNC_URL`, with no credential or participant data in that probe. Reachability does not prove a successful synchronization and is shown separately from the last successful sync timestamp.

## Security and operational limits

Only staff with the relevant server-side permission can view the operations dashboard or retry a ticket delivery. Django Admin model permissions govern alert-rule changes. Retry actions require a reason and enter the audit log. Delivery model records contain a contact hash rather than the ticket recipient address; notification recipient addresses remain in the protected operational database. Provider tokens are read from settings and never stored on delivery rows or displayed in dashboard error details.

The trailer's SQLite database, its encrypted backups, and central sync must remain protected as documented in the existing Build 7C-4B/4C guides. PostgreSQL row-lock behavior, real provider delivery, physical network interruptions, and production scheduler supervision have not been verified here. The attorney-approved consent PDF is still required before live registration.
