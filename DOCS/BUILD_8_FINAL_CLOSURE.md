# Build 8 — Final Closure

**Status: software-complete for the defined development scope; not production-ready.** This review closes Build 8D across participant qualifications, document management, and verification approvals.

## Delivered functionality

- A controlled qualification catalog and explicit mappings from published registration answers. Mapping provenance and source answers are retained; processing is repeat-safe and unrelated intake text is not inferred as a qualification. Staff can view, add, and update qualifications; BTW Admin alone configures catalogs and mappings. Self-reported and independently verified qualifications remain distinct.
- Private, participant-bound document uploads with metadata, qualification links, authorized preview/download, replacement history, archive state, and audit records. Supporting evidence alone does not verify a qualification.
- An authorized staff approval queue. Decisions preserve reviewer, time, evidence, outcome, reason, and history. Rejections require a reason. Relevant evidence/qualification changes and applicable expiry trigger re-verification while preserving earlier decisions and unrelated qualifications. Repeated or conflicting submissions cannot silently replace current state.

## Data, privacy, and product boundaries

Qualifications attach to canonical participants and catalog entries. Documents and evidence links are constrained to that same participant. Verification decisions are separate historical records. Existing participant, registration, ticket, consent, and Build 7 identifiers are preserved. Staff access and write actions enforce server-side permissions; administrative configuration is separately restricted and sensitive actions are audited. Private files use authorized application views, not public media URLs. Upload checks cover supported type/signature and configured size; filenames are not trusted as paths.

BTW has no event check-in, arrival tracking, attendance scanning, or checked-in status. Ticket scanning identifies a participant for an activity only. Build 7 synchronization does not transfer Build 8 qualification records, decisions, mappings, or document binaries. No participant portal or employer document access is included.

## Validation recorded

- Full Django suite: **277 passed, 1 skipped**; the skip is the PostgreSQL-only ticket allocation row-lock test (SQLite cannot validate PostgreSQL locking).
- Focused Build 8A–8C suite: **27 passed**.
- `scripts/build_7c4b_virtual_integration.py`: passed portable recovery, replacement-trailer sync, outage/restart, multi-trailer isolation, lost-ack retry, and uncertain-match scenarios.
- `scripts/build_7f_virtual_integration.py`: passed event-scoped trailer authentication and interrupted transfer recovery. These are Build 7 regression checks, not Build 8 data-sync tests.
- Django system check, migration consistency, Python compilation, and `git diff --check`: passed. No uncommitted schema changes were detected.

## Launch verification and future boundaries

Before launch: run PostgreSQL concurrency tests; configure and verify private production storage, access controls, backup/recovery, retention, malware scanning and deeper file inspection; validate physical trailer/network operation; validate real email/SMS providers; publish the approved Privacy Policy before requiring consent; and validate historical imports using authorized real files. No production credentials, real policy, live provider sends, physical hardware, or real historical files were used.

Build 11 participant-portal work and Build 12 privacy-reviewed synchronization of qualification data/evidence remain future integration dependencies. External credential checks, background checks, and reminder campaigns are out of scope.

**Completion:** Build 8 is software-complete for its defined development scope. The deployment and integration checks above remain; this is not a production-readiness claim.
