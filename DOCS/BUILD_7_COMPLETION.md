# Build 7 Completion — Software and Deployment Status

## Software implemented and tested

Build 7 establishes participant, registration, consent, ticket, and offline operations across the existing development workflow:

- Participant profiles and UUID identity; staff quiz-content and test-participant tools.
- Public and offline event registration, event-specific questions, groups/capacities, consent evidence, and existing 10-digit ticket/QR issuance.
- Trailer-to-central and central-to-trailer synchronization with trailer/event scope, idempotent replay, preserved participant and registration UUIDs, ticket identity, consent snapshots, and retry/recovery coverage.
- Encrypted portable SQLite backup and replacement-trailer recovery foundations; staff reconciliation for uncertain participant matches.
- Ticket email/SMS queues, operational alerting, and staff status tools, using mocked providers in automated tests.
- Historical CSV/XLSX import and export with preview, saved mappings, row errors, audited duplicate review, event/score provenance, sensitive-file storage, export filtering, and formula escaping.

Validation on the Build 7 feature branch: 253 Django tests passed; one PostgreSQL-only test was skipped. The virtual central/trailer runners passed using separate SQLite databases and HTTP processes. Django checks, migration consistency, Python compilation, and diff whitespace checks passed. These tests do not establish production readiness.

## Check-in policy discrepancy

Build 7F and Build 7D do not add event check-in or arrival scanning. The inherited application still contains legacy check-in functionality, including the Event Attendance model, a registration check-in URL, and station auto-check-in configuration. Therefore the codebase does not currently satisfy a claim that event check-in is absent. BTW's stated policy is not to use event check-in, arrival tracking, or attendance scanning; the legacy functionality needs an explicitly reviewed removal/disablement task before that absence can be claimed. This completion note does not remove historical schema or routes.

## Known limitations and deployment verification

These are verification/setup requirements, not missing Build 7 software features unless a check uncovers a defect:

- PostgreSQL row-locking and true concurrent ticket-allocation/synchronization behavior require a PostgreSQL run; SQLite integration is not a substitute.
- Physical trailer hardware, LAN behavior, scanner, printer, and replacement-machine operation require onsite validation.
- Real email and SMS provider credentials, deliverability, consent language, and duplicate-send behavior require provider configuration and controlled delivery testing. Automated provider tests use mocks.
- The approved, real Privacy Policy/consent PDF must be published through the authorized consent workflow before participant registration is enabled for production. No substitute legal text is included.
- Validate historical-file mappings and duplicate-review decisions against authorized source files; automated fixtures are synthetic.
- Production deployment must configure HTTPS, per-trailer credentials, separately protected backup keys/storage and retention, private upload roots, restricted staff permissions, monitoring, restore drills, and operational ownership.

## Cross-build identity and data boundaries

Registration and synchronization use stable Participant and EventRegistration identifiers; issued ticket numbers and QR payloads are preserved across transfers and retries. Uncertain identities enter staff review and are not merged automatically. Consent evidence is linked to its recorded version; historical imports do not create consent acceptances, official registrations, tickets, or official simulator results. No Build 7D/7F changes alter Build 6B.

Historical scores remain provenance-bearing historical records and do not introduce a new scoring formula. No Build 7D or Build 7F changes add attendance/check-in behavior.

## Future-build dependencies

Duck Hunt and simulator/game experiences consume the existing event/participant identity and progression contracts. Any later build must preserve ticket, consent, official/nonofficial, event-scope, and synchronization invariants, and must not introduce event check-in, arrival tracking, or attendance scanning under BTW's stated policy.
