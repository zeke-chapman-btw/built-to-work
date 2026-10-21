# Build 10C — Event Health Monitor discovery and approval boundary

## Confirmed scope and owner decision

The original October 21 Build 10 discovery names four phases: Pre-event preparation; Arrival and setup; Live event operations; Closeout and pack-up. Those names are historical direction, not approved check definitions, active-phase rules or readiness transitions.

During Build 10C the owner explicitly chose: **Keep policy-dependent features pending.** Therefore no required checks, verification workflow, readiness score, exception approvals or proceed/stop policy is implemented. The full Event Health Monitor is not complete.

## Actual architecture inspected

- Event lifecycle/editing: apps/events/event_management.py; Event dates/timezone/status and existing safeguards remain authoritative.
- Operations Setup/Templates: apps/operations/setup_presentation.py, template_services.py and template_models.py. Configured resources and immutable application provenance are not inspection evidence.
- Existing result/configuration observations: apps/events/experience_operations.py. Official/test exclusion, units, simulator review status and source permissions remain unchanged.
- Staffing: apps/staffing/services.py and its existing coverage/assignment validation. Pending requests do not count as confirmed coverage. No minimum readiness coverage policy is inferred.
- Workdays/Mileage and event reports: existing source/approval snapshots and reconciliation are financial/attendance records, not a closeout readiness sign-off.
- Tasks and notifications: existing task access/quick-create/audit mechanisms. No automatic task generation is introduced without approved deduplication/ownership rules.
- Offline preparation: existing event/trailer preparation, downloads/outbox and persisted checks concern registration workflows. They do not verify general connectivity, game/simulator synchronization or equipment conditions.
- OperationalIncident and BackupRecord exist, but their source scope is not an event-wide readiness/exception policy. Do not reinterpret a global incident, automatic provider probe or backup timestamp as a physical check.
- Simulator: existing configurations/capture validation/results remain; protected Build 6B XML code is not merged and no scoring/evidence formula is invented.
- Audit: reuse existing AuditLog/domain services for future consequential verification transitions after policies are approved.

## Implemented neutral foundation

apps/operations/health_presentation.py supplies a read-only event-scoped context under the same explicit event/setup viewing predicate as 10B Setup. templates/operations/health_foundation.html appears within Event Operations. It displays the pending-policy boundary, actual event lifecycle/last-change metadata and retained template provenance. Historical phase names appear only as planning information, with requirements pending.

There are no new Health Monitor models, migrations, mutation endpoints, evidence records, verification/exception roles, tasks, network probes or readiness calculations. Viewing the panel does not create or change records. General staff status or event assignment alone does not grant access. No “Verified” or “Exception approved” status is fabricated. Event last-edited time is explicitly not an inspection timestamp.

## Minimum decisions before the operational monitor can proceed

| Decision | Recommended review option — not approved policy |
| --- | --- |
| Phase operation | Retain the four historical names, then define entry/exit and manual versus scheduled transitions. Decide how current and prior phases are displayed and revised. |
| Mandatory checks | Define an event-type/resource-specific matrix: mandatory, optional or explicit not applicable; establish valid zero staffing requirements and checks that cannot be waived. |
| Verification authority | Use narrowly scoped Django permissions within approved existing groups; distinguish assigned responsibility from authority to attest. Do not automatically promote all staff or assigned event personnel. |
| Physical/digital evidence | Define the acceptable recorded observation, source and supporting evidence for each check. System configuration and successful HTTP health are not physical inspection. Specify attachment/privacy/retention rules before adding uploads. |
| Freshness and changes | Define expiry, event-time/clock-skew rules, affected-resource invalidation, rereview and whether offline attestation is supported. Do not introduce arbitrary expiry windows. |
| Failures and proceeding | Decide what unresolved failures do, which safeguards remain unwaivable, and whether any explicit authorized operational exception may allow proceeding. Do not equate an exception with equipment becoming operational. |
| Exception approval | Define request/approve/revoke authority, self-approval policy, justification/impact/mitigation, evidence requirements and expiry. No new business role or approval hierarchy is assumed. |
| Closeout and task links | Define pack-up/return obligations, unresolved sync treatment and final review; choose explicit Create Task versus approved deduplicated automated follow-ups. Financial reconciliation remains a separate domain. |

## Proposed future data direction — not implemented

Once the policies above are approved, consider versioned requirement definitions; event-scoped applicability; append-only verification/evidence attempts with actor/time/source and configuration fingerprints; separately auditable exception requests/decisions/revocations; and deterministic status derivation from current valid evidence. Preserve older attempts and snapshots, enforce event scope/FKs and explicit permissions in services, and reject stale updates. Do not choose detailed statuses/transitions or schema solely to make this build look complete.

## Verification boundary

Focused tests cover explicit setup-view permission, lack of authority from staff assignment, pending-policy presentation and repeated GETs creating no audits/tasks/application records. Existing 10B configuration/history and result separation remain in the regression suite. No physical equipment, real simulator, trailer network, evidence freshness or exception approval workflow has been certified. Owner approval is required to resume this policy-dependent workstream; independently authorized UI work continues.

## Build 10C verification and release boundary — 2026-10-21

Final SQLite regression: 954 tests run, 949 passed and 5 PostgreSQL-only tests skipped. The final focused PostgreSQL run passed all 121 tests, including the 10B template suite, 10C presentation/time safeguards and four real guard/concurrency tests. The 10B operations migrations were applied, reversed and reapplied on isolated PostgreSQL with representative event/station/game data unchanged and no template seeding.

The exploratory full PostgreSQL run was not green: 30 errors across existing communications/participant integration (24 closed-connection errors), qualifications (one nullable outer-join FOR UPDATE error), and SQLite-only trailer backup (five explicit unsupported-operation errors). An untouched archive of exact 10B commit 9a20d451d1b7040d0e3546f9d52c7c2988e35e61 reproduced the same 30 test names and error categories. This establishes baseline failures, not PostgreSQL-wide certification. No unrelated repairs or test exclusions were made. Review these limitations before recommending the combined preview release.

Frontend checks passed 54 assertions. System checks, migration consistency and tracked-diff whitespace checks passed. Browser verification used synthetic data in a separate private Codespaces port, including an unrelated event-description save preserving exact .123456 timestamps and confirmed assignment windows. The native date picker displays supported milliseconds and the unchanged server-side record preserves its exact original precision; changed schedules still pass the existing staffing and DST validation.

10B is committed locally only. 10C remains uncommitted for owner review. No push, merge or deployment is authorized by these results. The full operational Health Monitor remains pending owner policy. The existing /accounts/login/ redirect issue is outside this build and remains unchanged.

## Subsequent Event presentation refinement

Owner-directed refinement after the initial report extends Base44-inspired styling to Event edit forms, related menus and confirmation screens. It does not authorize Health Monitor policy, change readiness definitions, or alter the read-only boundary. The full SQLite suite again passed 949 tests with five PostgreSQL-only skips; the same 54 frontend assertions passed. Earlier isolated PostgreSQL results and reproduced baseline limitations remain applicable; PostgreSQL-wide compatibility is not newly certified by this presentation-only pass.
