# Build 11 — Participant Workspace Implementation and Verification Report

Date: October 22, 2026. Repository: `zeke-chapman-btw/built-to-work`.

**Delivery state:** staff participant workspace implemented and verified for owner review. Final acceptance remains subject to review of the intake/mapping gaps and the inherited concurrency issue below. **No merge or deployment is included.**

## 1. Executive Summary

Build 11 extends the existing participant system into a staff workspace for finding, understanding, maintaining and analyzing people. It connects canonical identities, current career information, private documents, qualifications, verification, original acquisition, event/assessment history, internal notes and existing tasks.

The actual Build 10C Event Workspace and subsequent 10D implementation govern the presentation. Existing domain services remain authoritative. There is no replacement participant database, document repository, credential-verification engine, scoring engine, customer module or CRM.

The owner's additional intake assignment was completed as a **read-only discovery audit**. No new intake mapping functionality, historical answer backfill or offline synchronization change is implemented. See [the intake audit](BUILD_11_INTAKE_AND_MAPPING_AUDIT.md).

## 2. Repository Baseline and Branch Verification

- Baseline: `c90baf984d621852b2fb847141fd28cf1d7ef0a2`.
- Isolated worktree: `/workspaces/btw-build-11`.
- Development branch: `build-11-participant-workspace`.
- GitHub repository: [built-to-work](https://github.com/zeke-chapman-btw/built-to-work).
- The existing source branch and remote `render-preview` pointed to the approved 10D checkpoint at discovery. Main is an older independent branch; no merge was needed to establish this baseline.
- No AGENTS.md was found in the repository, checked ancestors or tracked nested paths. None was created.
- The exact final pushed SHA belongs in the delivery receipt; it cannot be embedded as the hash of its own containing commit. The commit containing this document is the Build 11 review candidate.

## 3. Build 10D Continuity Confirmation

The baseline contains both the Event Workspace refinements and all six Build 10D workstreams. Build 11 leaves that Render Preview environment available for the owner's ongoing review.

No Render deployment, production change, deployment-branch update, environment configuration change, dependency change, merge or rebase is performed. The historical migration handoff, simulator/6B work and all unrelated branches remain protected. The verification compares the 26 pre-existing worktrees' HEAD, branch, status and fingerprints of tracked changes and nonignored untracked content; it does not claim a forensic comparison of every ignored runtime/cache file.

## 4. Existing-Functionality Inventory

The mandatory first-phase inventory is [BUILD_11_EXISTING_FUNCTIONALITY_INVENTORY.md](BUILD_11_EXISTING_FUNCTIONALITY_INVENTORY.md). It distinguishes already implemented, partially implemented, implemented elsewhere, not implemented and unsuitable existing capabilities, with source evidence and reuse decisions.

The later intake instruction and final implementation boundaries are recorded as an explicit addendum to that discovery inventory. Earlier implementation possibilities in the inventory are not claims that intake source transport or additional mappings were completed.

## 5. Functionality Reused Versus Newly Implemented

| Area | Delivery classification | Implementation / boundary |
|---|---|---|
| Canonical identity and normalization | Reused; staff read projection improved | Existing Participant, identity UUID, normalization and reviewed reconciliation links. No record merge or rewritten relationship. |
| Current participant/career profile | Existing foundation extended | Same Participant and career one-to-one; attributed staff editing, structured preferences and reported overall experience. |
| Work history | New participant-owned extension | Staff-recorded entries, dates, current role, source reference, revisions, audit and soft archive. |
| Qualifications and private documents | Existing engines reused; interfaces improved | Same records, storage, evidence links, provenance and approval decisions. |
| Original acquisition | New staff-managed extension | Event / Online / unknown, original date/reference, evidence-backed corrections and history. Automatic intake capture/transport remains a documented gap. |
| Discovery and saved views | Existing directory extended; saved views new | Validated criteria, safe sorts, pagination, private owner-scoped views and current permission checks. |
| Participant intelligence | New composition of existing facts | Server-rendered charts/tables and links to the same filtered population. No new scoring or unrelated platform reporting. |
| Event/assessment/activity history | Existing evidence integrated | Stored records, permission controls, factual timelines and explicit historical methods. |
| General internal notes | New because specialized existing notes were unsuitable | Staff-only notes; existing decision notes remain separate; core AuditLog reused. |
| Customer notes, portals, matching, CRM | Compatibility only / deferred | No customer-facing authorship, customer model, new portal, matching engine or CRM pipeline. |

See [BUILD_11_ARCHITECTURE.md](BUILD_11_ARCHITECTURE.md) for models, services, permissions and future boundaries.

## 6. Participant Identity and Profiles

Participant primary keys and immutable identity UUIDs remain authoritative. Existing active, resolved person-to-person identity links are read as a reversible projection in the staff directory and domain counts. Linked source rows retain their identifiers, documents, results and source relationships. Conflicting candidate links and cycles remain separate; contact hints are never automatically merged.

Profiles organize overview, career/work history, qualifications, documents, assessments, events, original acquisition, internal notes, factual activity and accessible tasks. The canonical current career profile remains authoritative; linked historical rows are not silently combined into new career claims.

Staff contact/career edits use existing normalized fields, allowlists, an audit reason and revision checking. Contact email does not silently change participant login email. New reported experience and preference fields remain blank/unknown on existing records. No DOB, demographic targeting or unnecessary sensitive profile fields are introduced.

Source: [identity.py](../apps/participants/identity.py), [workspace_models.py](../apps/participants/workspace_models.py), [workspace_services.py](../apps/participants/workspace_services.py), [workspace_views.py](../apps/participants/workspace_views.py).

## 7. Documents, Credentials and Verification

The existing private repository, categories, file UUIDs, source metadata, evidence links, replacement/archive history and audit remain intact. Existing signature/extension/size validation and permission-checked attachment downloads remain in use. There is no public file URL or new inline document execution surface.

The library, upload, metadata, replacement, category configuration, qualification management, approval queue/detail and internal-review screens use the participant wrapper over the established design components. Libraries and queues receive pagination; verification prefetches reduce repeated per-record queries.

Effective verification still comes from the existing service. Expired or replaced evidence and changed qualification snapshots do not remain silently verified. Material edits to a currently verified qualification now require explicit acknowledgement; the old decision remains retained and the changed claim requires review. A PostgreSQL nullable-join lock is restricted to the qualification row using `select_for_update(of=("self",))`.

An uploaded file, self-reported claim, credential review and internal participant approval remain distinct. No required-credential policy is invented from missing records.

## 8. Acquisition Source Tracking

A protected one-to-one original-acquisition record supports Event, Online and explicit unknown/unverified origins. It records an original time when supported, event/online source, submission reference, provenance, actor and attributable corrections. Later event registrations do not overwrite it.

Missing historical origins remain unknown; creation timestamps are not relabeled as original acquisition dates. Profiles, directory columns, advanced filters, charts and source-change history use the same record.

**Important limit:** the staff recording/correction workflow is implemented. Automatic source capture for every existing intake family, historical acquisition backfill and offline origin transport are **not** implemented. They are review items in the read-only intake audit, consistent with the owner's instruction not to implement new intake mapping functionality.

## 9. Directory, Search, Filters and Saved Views

The directory searches the event-independent talent pool, with active/archived choices and reserved system-test identities excluded from talent counts. Page size is 25; sorts are allowlisted and deterministic.

Filters cover supported contact/name, location, career/trade text, recorded skills/qualifications, experience, availability, employment preferences, travel preference, original source/date, event registration, effective credential/document status, assessment evidence/stored score units and defined profile data coverage.

Unknown values are distinct from negative answers or zero. One-to-many evidence uses canonical person sets/Exists rather than duplicating directory rows. Underlying permissions are enforced on direct requests, hidden chart criteria and saved views.

Saved views belong to one staff account, retain versioned JSON criteria and recheck current permissions whenever opened. They can be archived/restored without deleting their history. Forged IDs, revoked permissions, unsafe sort input, invalid dates/UUIDs and malformed chart selections are covered by tests.

## 10. Participant Intelligence, Tables and Charts

The participant intelligence page provides original-source distributions/trends, geography, reported interests and experience, recorded catalog skills/qualifications, employment interest, registration/participation, accepted assessment evidence, document/verification states and six-group profile data coverage.

Charts link to the same validated directory pipeline. A live browser drill-down from the Georgia segment matched its 15-person synthetic cohort. Automated tests compare segment counts with destination lists and retain other selected dimensions.

Population, registrations, unique registered people and recorded participation remain separate. Registration is not attendance. Summaries describe all relevant records for the filtered people across events; an event population filter does not silently redefine every metric as event-local.

Quiz values count stored correct answers, simulator values retain stored TOTAL SCORE percent and game values retain raw points. No historical answer is rescored. Game average/highest are unavailable when the cohort contains different game/version/configuration methods. Unknown scores are unavailable, not zero. Multi-valued evidence/result buckets can overlap and must not be summed into population.

Definitions are available in the interface and [intelligence.py](../apps/participants/intelligence.py). The Tracker was reviewed only as a UI/reporting reference where access allowed. No Tracker records were migrated, exported or copied into the build.

## 11. Event and Assessment History

Profiles connect existing registrations, event dates/locations/status/source, current QR relationships, coherent official experience sessions and retained legacy check-ins, subject to each domain permission. Authorized staff can register an existing person through the existing service without creating another identity or silently overriding registration policy.

Assessment sections show stored quiz, game, simulator and historical imported evidence with mode/status/version/method labels. Historical scores remain separate from current percentage metrics. Accepted summaries reuse the event-reporting source querysets factored into [activity.py](../apps/event_reports/activity.py); event performance semantics remain covered by regression tests.

History is bounded: most recent 50 records per assessment source, 100 relevant participation records per source and paginated registrations. These are explicit presentation limits, not deletion or recalculation.

## 12. Activity History and Internal Notes

The factual timeline composes existing domain timestamps and core audit entries, with up to 200 relevant facts and permission-gated sources. It does not fabricate attendance or create a duplicate activity store. It is not a complete import of all historical Django Admin log entries.

General notes are distinct from append-only participant review decisions and qualification verification decisions. Notes require staff permissions, retain original authors, attribute edits, require reasons/revisions, support idempotent creation and soft archive, and expose authorized history. Staff may edit their own notes; superusers may edit with attribution. A live authenticated browser submission succeeded in the isolated synthetic database.

Notes are unavailable to participant/customer experiences. Private note changes do not become readable merely through the general activity permission.

## 13. Customer-Specific Notes Architecture

No authoritative customer organization/membership model exists at the baseline. Build 11 deliberately does not invent one or use a free-text customer label as tenant authority.

Current ParticipantNote rows can represent only `btw_internal`, enforced by a database check and server-side scope rejection. Customer note creation is denied even for superusers. No customer-facing note endpoint exists.

Build 13 must supply authoritative organizations and employee memberships. Build 14 can extend the same participant-note foundation with required organization ownership, membership-authorized customer authorship, protected durable attribution, tenant-scoped reads/history and authorized BTW read-only access. BTW staff must never create notes on a customer's behalf. Participants and other customers must not receive those notes.

These are compatibility/security boundaries and documented future requirements, **not completed customer-note functionality**.

## 14. Build 12 Participant Portal Readiness

The existing account system already links the same Participant to a user. Build 11 retains that relationship and the existing limited self-service allowlist.

Future portal work can reuse current profile, work history, evidence and event foundations through separately authorized forms. Staff notes, internal evaluations and customer notes must stay private. Participant-submitted changes must preserve source attribution and the separation between claims and verified evidence.

No Build 12 portal implementation or broader participant self-service expansion is included.

## 15. Build 16 CRM Compatibility

The same Participant, existing TaskParticipantLink, permission-filtered tasks, source references and factual activity support future participant/customer relationship management.

Build 11 does not create a second CRM, recruiting pipeline, matching engine, calls/emails/meetings store, communications automation or customer-participant relationship module. Builds 13–17 retain their stated boundaries.

## 16. Build 10C UI/UX Compliance

The participant wrapper extends `operations/design_base.html` over the approved internal shell. It reuses Event Workspace layout, cards, content rails, navigation, actions, form hierarchy, status language and responsive behavior. Added CSS is scoped to participant pages. No competing framework, public shell or kiosk design is introduced.

Profile tabs use contained horizontal navigation at narrow sizes; data tables may scroll inside their own accessible regions. Long forms retain vertical scrolling, labels and error feedback. Mobile identity spacing was refined during visual review.

Intentional exceptions: pre-existing account requests, historical import administration and form-builder screens were not redesigned merely because they are reachable from older workspace navigation. The established participant workspace landing route is preserved for continuity.

## 17. Security and Permissions

All new staff endpoints require authenticated, active staff with participant-view permission. Domain permissions separately control documents, qualifications, registrations, QR tickets, quizzes, simulators, games, historical scores, approvals and internal reviews. Filters and charts cannot bypass those permissions.

New analytics, activity, acquisition-management and participant-note permissions use the existing Django authorization framework. No operational users, groups, privileges, authentication configuration or credentials are changed. Saved views are owner-scoped regardless of generic model privileges.

CSRF protection, private storage, attachment/no-store/nosniff/sandbox download behavior and escaped authored text remain in place. The candidate review found no credentials or high-signal secret patterns, operational datasets, configuration files or unrelated changes. Development fixtures use synthetic data; generated local review credentials are outside Git.

## 18. Database Migrations and Data Preservation

Migration: [0015_participant_workspace_11.py](../apps/participants/migrations/0015_participant_workspace_11.py).

It adds four career fields, four participant-owned extension tables and permissions/constraints. It contains no speculative data backfill, identity rewrite, score recomputation or operational database migration.

An isolated PostgreSQL 18.6 upgrade test seeded retained participant/career, registration, QR, qualification, document-reference, verification and audit data before the new migration. **All 114 existing tables retained their row counts and original-column fingerprints; 47 existing rows were unchanged.** Identity UUIDs and source relationships were preserved. New extension tables stayed empty and historical acquisition stayed unknown.

Private file storage was not moved or duplicated. Separate temporary-file tests verified uploads, metadata, secure downloads, evidence links and permission boundaries.

Application checks pass and `makemigrations --check --dry-run` reports no changes detected. Operational Render and production databases were not used for development tests.

## 19. Offline Operations and Identity Continuity

Offline intake, QR allocation/recognition, station/kiosk activity, Duck Hunt, simulator ingestion/scoring, outbox synchronization, downsync and reviewed reconciliation use their existing implementations.

Build 11 changes no intake form, question-publishing implementation, qualification mapping engine/signal, offline protocol or scoring runtime. Existing contact hints remain reviewable rather than automatic merges. Online people can be selected for subsequent staff event registration through the same identity.

Automated offline/sync/reconciliation coverage is included in the full SQLite suite and focused PostgreSQL verification. No physical trailer, scanner, simulator hardware, disconnected deployment or real-provider delivery was tested.

**Inherited ticket-allocation concurrency finding (not repaired in Build 11):** an additional PostgreSQL run of the five SQLite-skipped cases completed all five assertions successfully, but normal database teardown failed because four test sessions remained open. A repeat using a retained isolated database exposed an intermittent unique-validation failure during concurrent first creation of TicketAllocationBlock. The unchanged historical baseline passed three ordinary runs; a separate controlled two-thread interleaving then reproduced the same failure on exact baseline commit `c90baf984d621852b2fb847141fd28cf1d7ef0a2`. The baseline and Build 11 ticketing.py, events/models.py and original ticket test sources were byte-identical. This demonstrates an inherited race, not a verified Build 11 fix. A first-use allocation request can fail when another worker creates the same block between lookup and model validation; no duplicate ticket number was observed in the diagnostic. The isolated tests and database-cleanup limitation are recorded openly. No operational database was involved, and no unrelated ticketing repair is included.

## 20. Functional and Regression Test Results

| Check | Result |
|---|---|
| Complete Django suite, isolated SQLite | **1,065 tests; successful; 5 PostgreSQL-only skips.** |
| Focused PostgreSQL 18.6 suite | **139 tests; successful; no skips.** Includes Build 11, participant integration, reporting, offline registration, sync, downsync and reconciliation. |
| New Build 11 tests | **23**, covering management, revisions, permissions, notes/privacy, source stability, discovery/saved views, chart/list consistency, effective verification, stored evidence, identity projection and page rendering. |
| Frontend scripts | **All 5 pass:** notifications, navigation focus, task modal, shared UI patterns and Duck Hunt logic. |
| Application checks | Pass; no system-check issues. |
| Model/migration consistency | Pass; no missing migrations. |
| PostgreSQL preservation | Pass; 114 existing table fingerprints/row counts preserved. |
| Complete diff / scope / protected state | Reviewed; exact file staging and remote verification are recorded in delivery evidence. |

SQLite's five skips are the ticket allocation concurrency case and four Operations PostgreSQL locking/guard cases. Their additional PostgreSQL execution and any cleanup/race limitation are described above; a nonzero cleanup run is not represented as a wholly successful command.

The legacy participant integration test previously called FileResponse.close directly inside TestCase, closing its PostgreSQL transaction connection. Its test-only cleanup now consumes Django's streaming iterator, preserving proper closing behavior and checking PDF bytes. Application download behavior is unchanged.

Earlier development failures were corrected and rerun: mixed text query typing, a geographic annotation colliding with Django model state, saved filters losing GET criteria, and navigation/search-label compatibility. Passing results above refer to the final functional implementation, not those earlier failing attempts.

## 21. Visual Verification Results

Regular Chrome was used with an isolated SQLite database containing only clearly marked synthetic fixtures. The verified private Codespace server uses port 8027; it is separate from Render and older development servers.

**42 route/state observations at 1440×900 desktop and 390×844 mobile, plus five 768×1024 tablet checks: 89 unique observations.** No unexpected 404/500 screen or page-level horizontal overflow was found. Contained table/tab scrolling is intentional.

Coverage includes directory/advanced filtering, invalid/empty results, saved-view archival, profile creation/editing, work history, acquisition correction, notes/history/archive, existing-person event registration, document library/edit/replace/categories, qualification editing, approval queue/detail, internal review, empty profile and every profile tab. Screenshots and contact sheets are retained in local delivery artifacts. Below-fold charts and longer workflows received additional inspection.

Browser interactions verified a chart segment's exact filtered cohort and a real authenticated/CSRF-protected note submission. Automated tests cover additional successful/invalid posts, stale revisions, permission denial, forged ownership and cross-record requests. No claim is made that every possible production configuration, screen-reader combination or real hardware interaction was tested.

## 22. Development Workflow Continuity

Work proceeded on one isolated branch from the verified deployment checkpoint. Existing documentation and actual source were inspected first; current design implementations governed new UI. Extensions were additive and existing services reused.

Testing used isolated SQLite, PostgreSQL and private file locations. The full diff, exact candidate file list, secret/scope review and all protected worktree fingerprints were reviewed before preservation. No intermediate commit/push was made. No existing dirty checkout was reset, cleaned, switched or overwritten.

The additional intake audit remains read-only. No build numbering, roadmap, hosting, development strategy or future-module scope was changed.

## 23. Git Commit and Push Verification

The authorized delivery is **one reviewed commit** on `build-11-participant-workspace`, pushed to its corresponding GitHub development branch. It is not merged into main or render-preview and is not deployed.

Before commit, the staged path set must equal the reviewed Build 11 list, the staged diff must pass whitespace/scope checks, and no application configuration, generated credentials, runtime database, local report screenshot or unrelated file may be included. The post-push receipt records the exact SHA, remote equality, clean worktree status, unchanged protected worktrees and unchanged unrelated remote branches.

The historical handoff branch remains independent and untouched. The final local HTML report and owner-facing receipt supply the actual push result rather than inventing a self-referential commit hash here.

## 24. Outstanding Issues and Deferred Features

**Review before final Build 11 acceptance:**

1. Agree the next intake mapping work from [the read-only audit](BUILD_11_INTAKE_AND_MAPPING_AUDIT.md): field-by-field profile mappings, canonical email/type/length validation, construction versus overall experience, credentials versus free text, travel unknown/no, structured education/work history and deployed question/version inventory.
2. Decide trusted returning-person intake behavior. Current independent anonymous online submissions can create separate identities; request-key retry protection is not person-level identity proof.
3. Approve original-source capture rules for online versus physical event intake and any offline source transport. Existing staff acquisition tracking works; missing origins remain unknown.
4. Approve mapping versioning/historical replay and offline question/answer-shape parity before changing or backfilling anything.
5. Review the inherited PostgreSQL ticket-allocation concurrency finding and test-database cleanup limitation. No unrelated ticketing repair is included.
6. Benchmark large deployed cohorts and evidence-heavy status filtering before promising a performance SLA. Directory paging is bounded and synthetic scale tests pass; production-volume load testing was not performed.

**Intentionally deferred:** Build 12 portal expansion; Build 13 organizations/membership; Build 14 customer-authored notes/portal; Build 15 matching; Build 16 unified CRM; Build 17 broader reporting; Tracker/Base44 migration; physical hardware testing and the previously discussed future kiosk screen administration.

**Existing limitation retained:** legacy routes that rely on the default `/accounts/login/` redirect may still encounter the previously documented login destination issue. Build 11 does not add that URL or silently alter application authentication configuration.

The candidate is ready for owner review with these boundaries disclosed. Acceptance, merging, deployment and any intake mapping implementation remain separate owner decisions.
