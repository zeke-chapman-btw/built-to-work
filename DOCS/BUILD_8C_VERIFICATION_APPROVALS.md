# Build 8C — Verification and Approvals

## Scope

Build 8C adds an auditable staff workflow for reviewing participant qualifications and supporting documents. Uploading or linking a document does not, by itself, verify a qualification or document.

## Workflow

- Authorized staff use the **Approvals** queue and item detail pages to review pending qualifications and active participant documents.
- A decision records its status (pending, verified, rejected, or expired), review method, reviewer, timestamp, required reason, optional evidence notes, and any linked evidence document.
- Rejected decisions require a reason. Self-reported qualifications remain visibly self-reported until independently reviewed.
- Decisions are append-only. New decisions link to the prior record, and request keys make repeated submissions idempotent.
- A verified qualification is displayed as pending again when its material qualification snapshot changes or its supporting document is replaced, archived, or expired.
- An expired document cannot be approved. Evidence documents must belong to the same participant as the qualification being reviewed.
- Staff qualification and document pages show the current verification status and relevant decision metadata/history.

## Access control

The queue and decision endpoints check authorization on the server. Viewing approvals requires active staff with the view or review permission; submitting decisions requires the review permission. Django superusers are allowed. Unauthorized requests receive HTTP 403. The navigation link is shown only to staff with the applicable permission.

## Validation and operational limits

Synthetic document fixtures are used in tests; no real consent or qualification evidence is fabricated or pre-approved. Verification records preserve the reviewer’s decision history, but application deployment still needs normal staff account and permission administration. The project’s document-storage and file-validation limits documented for Build 8B continue to apply. Database row locks protect concurrent decisions where supported; SQLite development tests do not prove PostgreSQL locking behavior.

This is a development-scope workflow, not a production-readiness claim. PostgreSQL concurrency, production private-storage configuration, and operational permissions remain deployment verification items.
