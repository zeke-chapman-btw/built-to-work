# Build 7F — Central-to-trailer event preparation

This is development work on the Build 7 feature branch. Do not deploy it before security, recovery, and hardware review.

## Preparing an event centrally

A superuser uses Django Admin to provision a distinct TrailerInstance credential (entered once and stored as a Django password hash), then creates an OfflineEventPreparation for that trailer and Event. Assign nonoverlapping primary and backup ticket ranges. The Event must have an approved consent document with its original private PDF and a published registration form version. Central credentials can be rotated by entering a new value in the Admin form, or revoked by setting the trailer inactive/revoked. The credential is never rendered in Admin after submission.

On the trailer, configure BTW_TRAILER_IDENTITY, BTW_SYNC_API_TOKEN, and BTW_CENTRAL_DOWNLOAD_URL (the central /events/sync/download/ endpoint) outside source control. Run python manage.py sync_central_events --once or schedule the command. The worker requires HTTPS except for loopback HTTP in DEBUG. It discovers only events prepared for its authenticated trailer and downloads event settings, groups, event questions, published form snapshots, approved consent PDFs, and public pre-registrations. Credentials go in the Authorization header, never the URL.

The trailer stores each download page and signed cursor in CentralEventDownload and CentralEventDownloadPage. Failed transfers retain staged pages; the worker retries with bounded backoff. A complete snapshot applies in one database transaction and advances the checkpoint only after all records are accepted. Repeating the download preserves participant identity UUIDs, registration UUIDs, ticket UUIDs/numbers/tokens, consent evidence, group assignment, custom answers, submission snapshots, and original timestamps. Participant identity matches use the permanent UUID only; uncertain contact matches are not merged. A newer conflicting local record causes a visible failure for staff review. Local ticket range counters never move backwards.

The existing staff ticket recovery page can find an imported ticket by number, name, phone, email, or event, and redisplay/reprint it. It does not allocate a replacement ticket. Arrival/check-in is outside this build.

## Readiness and operations

The offline preparation detail page displays download status, last successful transfer, last advertised central pre-registration count, local downloaded count, and transfer errors. If a central download is incomplete or last advertised registrations are missing locally, activation is blocked. A count can reveal later registrations only after the trailer has successfully contacted central; no offline device can know about new online registrations during an outage. Legacy manually prepared offline Events retain their existing preparation path.

The staff should verify that the approved PDF is present locally, the right Event and trailer are selected, both reserved ranges are valid, and the downloaded pre-registration count is current before activation. A fully offline trailer continues serving its local registrations when central cannot be reached.

Central ticket reissues include prior numbered ticket history. The trailer invalidates the old local ticket, imports the replacement with its original number and QR token, and can safely replay the same download. Centrally voided registrations remain in the incremental feed so the trailer records the cancellation and revokes the original ticket; replay does not create a replacement ticket.

## Verification

- Focused tests: python manage.py test apps.events.test_downsync_7f
- Isolated HTTP integration: python scripts/build_7f_virtual_integration.py
- Existing trailer-to-central integration: python scripts/build_7c4b_virtual_integration.py

The 7F runner creates separate temporary SQLite databases and private media directories for central and two trailers, starts a central HTTP server and a trailer HTTP server, tests scoped credentials, resumes a staged page after central outage and worker restart, checks both trailers' imported tickets/consent/groups/answers, incrementally downloads a new registration and a later central cancellation, confirms ticket invalidation, and replays a lost local checkpoint without duplicates. Its data and credentials are synthetic and removed after the run.

## Deployment limitations

This verifies software behavior with SQLite and loopback HTTP. It does not validate physical trailer networking, TLS termination, PostgreSQL concurrency, large-event throughput, media backup, or a production legal PDF. Production must supply HTTPS with trusted certificates, protected per-trailer secrets, centrally allocated nonoverlapping ticket ranges, private media backup/recovery, and operational monitoring. Changes to active local event question configuration are deliberately rejected for staff review rather than silently replacing it. No automatic uncertain-identity merge is performed.
