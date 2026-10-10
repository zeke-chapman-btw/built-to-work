# Build 7C-2: consent and event tickets

## Consent document administration

A BTW superuser uses Django Admin's Consent document versions workflow to upload the original PDF. Upload validates the PDF, extracts its text, and creates a draft. The administrator reviews and may correct the extracted text, previews the participant display, explicitly confirms it against the PDF, and then approves and publishes. Publishing records the administrator, timestamp, document version, and SHA-256 hash of the original. Published versions and recorded acceptances are retained for history. The original PDFs are stored under PRIVATE_CONSENT_ROOT, outside MEDIA_ROOT, with no public storage URL; only the authorized Admin PDF view serves them.

The registration form renders the complete currently published text and requires an unchecked consent box. Registration is unavailable until a document is approved. The real Privacy Policy is intentionally not uploaded yet. The administrator will upload it through the application later. Synthetic PDFs are used only in automated tests. Production deployment must persist and protect PRIVATE_CONSENT_ROOT outside any web-served directory; backup and access policy for that private storage must be configured.

## Registration and tickets

A form-specific request key makes repeated submission idempotent. Participant, submission, consent acceptance, Event registration, and ticket issuance occur in one transaction. Ticket allocation uses ten-digit numbers from configured blocks, rejects overlapping blocks, and reports exhaustion. The QR payload is exactly tel: followed by the ten-digit ticket number. The downloadable PNG is a complete branded ticket with Participant name, Event, date where available, number, and QR code.

Authorized staff can search by ticket number, Participant name, phone/email, and Event, redisplay the existing ticket, and reprint without allocating a new number. Recovery and reprint actions are audited. A public ticket number alone does not open private Participant information.

## Validation and operational limits

Automated tests cover consent upload/review/verification/publication, authorization, malformed PDFs, registration replay, ticket content, recovery, and allocation. The QR in a rendered ticket was also decoded independently as tel: followed by ten digits. SQLite supports the ordinary suite but cannot validate PostgreSQL row-lock behavior. The concurrent allocation test is PostgreSQL-only and skips on SQLite; run it against a PostgreSQL test database before production deployment. Overlapping block creation is validated in application code, and operational controls should prevent simultaneous conflicting block configuration. Full image decoding/validation remains a preproduction hardening task.

## Deployment and simulator handoff
Set PRIVATE_CONSENT_ROOT to a durable private filesystem path outside the web root, configure backups and restrict OS access before uploading real consent documents. The default path is for development only. Do not publish participant registration until the attorney-approved policy is uploaded, reviewed, and published through Admin.

Quiz and Duck Hunt use the event-scoped ticket resolver. Simulator ingestion accepts a current ticket number for its configured Event while retaining the legacy phone identifier path; conflicting identities require staff review. Run the PostgreSQL allocation concurrency test and a physical simulator ticket-to-result walkthrough before production.
