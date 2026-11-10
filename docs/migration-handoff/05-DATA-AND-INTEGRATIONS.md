# Data and integrations

## Authoritative data and relationships

| Domain | Authority / preservation rule |
| --- | --- |
| Identity | accounts.User is authentication. participants.Participant is the canonical participant record with stable identity_uuid. staffing.StaffProfile is operational employee identity with an optional explicit User link. Never merge these identities by email alone |
| Events | Existing Event UUID and authoritative start_at/end_at govern the activity/ticket window; setup/travel/logistics do not silently extend it |
| Registration | EventRegistration links Event and Participant; groups/custom answers, versioned submissions, consent evidence and ticket history retain provenance |
| Experience/results | ExperienceSession/Activity, QuizAttempt/Section/Response, GameSession and SimulatorCapture preserve official/test/demo, replacement, skip and void distinctions |
| Qualifications/evidence | Catalog/mappings, ParticipantQualification, private ParticipantDocument and historical verification decisions remain participant-bound; a document is not automatic verification |
| Staffing | Plans/needs, requests, confirmed assignments and AssignmentDay revisions preserve original offers/decisions and replaced/removed history |
| Compensation | Effective-dated rates feed retained Workday/MileageTrip snapshots. Approval/current state controls reporting; settings changes do not rewrite approved history |
| Operations/reporting | Tasks/notes/documents reuse events/staff. Expenses and distributed incentives are separate ledgers; CostReview fingerprints sources. No copied payroll ledger |
| Customer/recruiting | No current customer/employer/campaign/position entity found in 9F; future associations must be explicitly designed |

UUIDs, database primary keys, ticket numbers, submission IDs and externally meaningful identifiers have different roles. Preserve types and leading zeros; do not assume all IDs are UUIDs. In particular, the separate 6B XML contract treats tel: plus ten digits as a **ticket identifier**, while the older 6A/OCR matching contract normalizes an identifier via participant phone rules. This is an unresolved future integration boundary; never connect the two by an undocumented cast or global lookup.

## Privacy and access

Participant contact/DOB, career data, qualifications/evidence, staff addresses/notes, compensation and consent history are private. Handoff verification inspected schema/source and synthetic evidence, not confidential rows or document bytes. Do not include databases, raw captures/XML, exports of people, private media, backups, .env, local config, credentials or session state in migration documents.

Use existing server authorization and least-privilege scopes. General staff self-service does not grant participant records, manager-private notes, another employee's pay, financial reporting or global settings. Customer access to internal profiles/documents is not implemented. Private file downloads require application authorization; never substitute public media links. Export protections include permissions, formula-safe strings and data minimization. Audit history may contain sensitive data and is not a public log.

## Existing communications and sync

- apps/events/communications.py provides immediate ticket delivery/operational incident queues with consent and opt-in checks, destination integrity, idempotency and uncertainty handling. deliver_communications is an explicit worker command, not evidence of an installed scheduler. Preview declares an in-memory email backend. Real provider delivery remains separately unverified.
- apps/events/offline.py, sync.py, downsync.py and related views implement event-scoped central/trailer preparation, idempotent upload, resumable downsync and review-safe possible matches. Identity links preserve rows/tickets instead of moving history destructively.
- scripts/build_7c4b_virtual_integration.py and build_7f_virtual_integration.py exercise virtual HTTP/SQLite systems. They do not certify physical LANs or replicate every later domain.
- Build 9B logistics/publication, staff/private data and 8–9F additions are not silently added to old wire allowlists. A future offline expansion requires explicit protocol/privacy planning.
- SQLite encrypted backup/portable restore is implemented with a separately held encryption key and manifest binding. Restoration targets an absent new database, not an overwrite. PostgreSQL backup/recovery and production rehearsal remain separate work.

## Simulator and hardware continuity

**Deployed 9F:** the Build 6A receiving API and accepted official result semantics exist. Profiles bind capture geometry/version, Event and Station. Only validated accepted official results may advance progression/reporting. No Windows helper is present in 9F's tracked tree.

**Separate branch:** cached origin/build-6b-simulator-results at 2b4ae2b705825d6dbe16a3b4728387d163a48672 contains simulator_helper/gui.py and xml_results.py plus docs. Normal workflow watches SimU ExperienceMode XML, reads stable files without altering simulator files, archives original bytes and records a local SQLite index. Repeated observations of one file version deduplicate; distinct files with identical content remain distinct evidence. Unsafe XML constructs are rejected; malformed/uncertain data remains for review. Scores stay Pending Formula. No Django synchronization or official score completion is part of that checkpoint.

**Separate dirty work:** /workspaces/built-to-work has older OCR helper/calibration-import files and build6b-helper.zip. The newer committed 6B XML direction supersedes normal OCR monitoring in that branch. Preserve all work but do not merge or deploy it without review.

Physical requirements previously discussed include a Windows kiosk/laptop, roughly 32-inch landscape touchscreen, QR scanner, Sinden lightgun, SimU simulator PCs and trailer-local networking. Historical calibration observed a particular NVIDIA Surround geometry/scaling; those observations are not universal constants. Validate actual screen topology, scanner focus, touch reach/readability, lightgun calibration, printing, local server access, offline resilience, startup/permissions and durable local storage before field acceptance. The current Codespace cannot certify those devices.

Windows-helper runtime/configuration names and paths belong to the separate branch; inspect its example **keys**, not private values. Older OCR diagnostics reference SIMULATOR_TESSERACT_EXE and SIMULATOR_INGESTION_TOKEN; the XML monitor's local configuration includes source/archive/index/polling keys. Copying a local XML database into Django is not an approved migration strategy.

## HubSpot — planned, no connector implemented

Recovered “HubSpot Integration Architecture” discussion supports this product direction:

- BTW keeps canonical participant identity, complete event/activity history and customer authorization.
- HubSpot is intended for candidate communication, post-event follow-up, intake, segmentation and recruiting engagement.
- The owner explicitly wanted aligned fields across BTW forms and HubSpot, and intake/updates synchronized back to BTW so authorized customer views and future candidate bundles can use them.
- Participant accounts should be in BTW, without requiring participants to hold HubSpot logins.

Proposed architecture details include stable BTW/HubSpot ID mapping, an explicit data dictionary with types/options/ownership/sync direction, provenance and conflict handling, summarized CRM properties without flattening event history, and consent-aware communication. These are design direction/proposals, not a deployed sync contract. No HubSpot app/client/model/routes were found in 9F. Authentication method, field map, conflict precedence, retries, deletion/retention and permitted sharing need a later approved integration brief. Do not send real records as part of this handoff.

## MoZeus / external analytics

**Latest owner decision:** BTW is moving away from MoZeus; do not spend further handoff effort reconstructing it. No MoZeus connector was found in the active tracked source. No required ongoing dependency or automatic analytics feed is assumed. Any eventual replacement, data import or legacy retention decision belongs to a separately approved scope. Native 9F reporting reads Django's existing records.

## Migration considerations

This is an account/workspace transition, not a database migration. Preserve existing identifiers, histories, data roots and branch lineage. Owner will handle new-account access. Before any later migration/deployment, identify actual runtime database/settings, verified backups and file durability; do not infer them from defaults. Git alone omits ignored databases/media/secrets and uncommitted local work. Transfer approved project documents separately and secrets only through the owner's established secure channels. Nothing in this package authorizes data exports, copying private records, schema changes or provider changes.
