# Build 10D — Operations, Platform Tools and Event Execution

This document records the combined delivery of the previously uncommitted Event Workspace refinements and Build 10D. The implementation baseline was commit `0ae49d2391864b0d76bb1e66eca3a514a6ed4c3e`; the delivery branch is `build-10d-operations-execution` in `zeke-chapman-btw/built-to-work`. The commit containing this document is the delivery checkpoint. No merge or Render deployment is part of this build.

## 1. Executive Summary

The six authorized workstreams deliver a common internal Operations presentation, existing quiz/station management screens, a permission-filtered Platform Tools directory, event execution, independent event checklists, and task-linked closeout. The approved Event Workspace implementation is the continuing UI standard. Existing business engines remain authoritative.

## 2. Event Workspace Preservation

The build began with 49 uncommitted Event Workspace files. Their original contents were recorded before implementation. They are included in this delivery; integrated task, event-navigation and service files extend those changes. The staffing role refinements, event forms, expenses/report projections, registration workflows and shared presentation components remain present.

Staffing requirements are Staff and Driver per day. Staff Lead counts as Staff; Combined means Driver + Staff and counts toward Driver only, once per assigned day. Only requested roles are displayed; fulfilled requested coverage is green, zero requirements are neutral, and unknown legacy roles require review. Workday, pay and mileage calculations are separate.

## 3. Operations UI/UX Modernization

Operations now uses the approved cards, headers, bound fields, validation summaries, action bars, disclosures, status badges and responsive tables. The landing page includes the next authorized upcoming event and active/open-closeout workflows. Global execution lists retain Operations navigation; event execution pages retain the Events workspace and event tabs.

The screen inventory is [BUILD_10D_SCREEN_INVENTORY.json](BUILD_10D_SCREEN_INVENTORY.json). Existing routes were retained. Four missing management templates found during inventory were supplied: platform monitoring, match list, match detail and match error fallback. Identity-review validation now stays with its bound detail form.

## 4. Operational Setup & Management

Quiz/category/question-set lists support bounded search, allowlisted filters/sort and pagination. Category, Draft version, question/answer, publication/scheduling, retirement and restart interfaces use shared patterns. Content eligibility and scoring still use the original full data, independently of filtered visible rows. Published content and historical assessment snapshots retain their existing rules.

Station lists, display settings, assignment/order controls, active-context confirmations and validation were modernized. Existing station identifiers, activation policies and participant runtime remain unchanged. Trailer display settings expose identity read-only, display name and active status to superusers; credential creation/rotation remains in restricted administration.

Approved Operations Templates and event resource forms are retained. Existing game/registration-form administration stays at its permission-checked destination; this build does not create a replacement game editor or reskin Django Admin.

## 5. Platform Tools

Platform Tools is a main sidebar destination visible only when existing tool permissions permit it.

| Tool | Existing URL / authority | Destination rationale |
| --- | --- | --- |
| Platform monitoring | `/events/operations/`; existing sync-view permission | Global synchronization, recovery, existing delivery and incident information |
| Participant match review | `/events/operations/matches/`; existing review/resolve/reversal rules | Global reversible identity review; eventual participant-reconciliation home remains a future decision |
| Backup records | Existing restricted Django Admin | Existing read-only record directory; no backup or restore job added |

Event preparation, quizzes, stations, assessments and templates remain in Operations. Monitoring was removed from the Operations directory and its existing Quick Action label was clarified. Existing URLs continue working. No restricted tool receives broader permissions.

## 6. Event Execution

`EventExecution` extends an Event with one primary manager drawn from existing active `StaffProfile` records with active staff logins. Event administrators appoint/reassign the manager with revision checks; changing an existing manager requires a reason. The primary role is event-scoped and does not grant global event, financial, staffing or task administration.

The interface links existing Event Info, Staff, Operations, Results and permitted setup workflows. An assigned employee can read their event checklist and complete their own items. Primary managers and authorized event administrators can manage the event checklist.

The existing lifecycle remains authoritative: “Mark Ended” uses the existing `completed` status and separately records actual end/actor. Current/live timing remains schedule-derived; no duplicate Live/Ended enum or unsupported Start workflow was added. Scheduled end never automatically ends an event. `end_reminder_candidates()` provides data for a later notification build without sending or scheduling reminders.

## 7. Health Monitor

The administrator-controlled BTW master has categories, phase, items, optional/required designation, order and active status. Four familiar phases receive eight small **optional** starter items; this is not a mandatory operational policy.

New events created through the existing staff creation service or Event Admin receive independent snapshots. Existing/imported events are initialized explicitly when their manager is assigned; GET requests never backfill or write records. Later master edits affect future snapshots and do not overwrite existing event wording, responsibilities or history.

Managers may add/edit/reorder items and categories, assign eligible event staff, complete on behalf of others, and archive optional items with a reason. Required master items retain their origin and cannot be downgraded, removed, or moved out of their required phase. N/A requires a manager/admin reason and preserves the item and audit history. Assigned staff cannot restructure or approve N/A.

The indicators are **Ready to Go?** (preparation + arrival), **Live Operations**, and **Closeout**. They show completed/applicable counts, exclude N/A and archived items, and show a neutral state when nothing is applicable. Incomplete work never rounds to 100%. These counts do not certify safety, equipment or offline readiness.

## 8. Event Closeout

An explicit actual end creates one existing `Task`, assigned to the primary Event Manager and associated with its event checklist. Event-row locking and the execution/task relationship prevent duplicate creation. Generic task edit/status/archive/link services reject changes to a managed closeout task; ordinary tasks retain their existing policy. Existing task comments/history remain compatible.

Formal closeout requires a recorded actual end and completion or reasoned N/A for applicable **required Closeout** items. Optional items and earlier phases do not prevent ending. Closing records actor/time, completes the same task and locks ordinary checklist edits. An ended event with an open closeout cannot be archived until closeout is recorded.

Authorized reopening requires a reason, records actor/time, unlocks corrections and reopens the same task. It does not restart participant operations or change the existing Completed lifecycle. Closing again resolves that task. Manager handover preserves the original closer's attribution on an already completed task.

Historical Completed events have no guessed actual end. An authorized manager/admin must enter a known actual end with its source/reason; future and ambiguous local timestamps are rejected. The actual end and reason are audited.

New automation audits use `event.execution.*`, including `event.execution.task.*`, rather than the existing notification-producing `operations.task.*` prefix. No new in-app/email/SMS delivery, reminder scheduler or notification transport was added.

## 9. UI/UX Standards Compliance

The standing standard is [BTW_UI_STANDARD.md](BTW_UI_STANDARD.md), extending [EVENT_WORKSPACE_UI_PATTERNS.md](EVENT_WORKSPACE_UI_PATTERNS.md). The new Operations wrapper reuses the Event Workspace stylesheet and components; its small additional stylesheet is scoped to internal Operations components. There is no competing component library.

Desktop and 390-pixel mobile browser review includes Operations/Platform Tools directories; quiz lists and secondary authoring forms; station list/detail/forms and assignment/version disclosures; trailers; offline preparation; monitoring/reconciliation; execution; master/event checklist editing, N/A/history; closeout confirmations, task status, locking and reopening. Functional role tests independently exercise hidden controls and direct-URL authorization. See the inventory and the local completion report for the actual evidence and coverage limits.

Mobile review found navigation overflow on the new grid wrapper. It was corrected with a constrained grid column and navigation width; final mobile evidence fits the page, with horizontal scrolling confined to the event tabs/table containers.

Intentional exceptions: participant/kiosk/game/station runtime retains its established presentation and behavior; question preview intentionally uses the actual kiosk view. Restricted Django Admin editors and backup records retain Django Admin. Existing Tasks stay in their dedicated workspace; the changed task detail reuses Event Workspace form/card styling. No custom asynchronous loading layer was introduced for server-rendered forms.

## 10. Database and Migration Review

The combined delivery includes inherited `staffing.0002_daily_role_coverage`, additive `operations.0007_event_execution_checklists_10d`, and `operations.0008_starter_master_checklist_10d`. Operations adds six models: master/category/item and execution/event-category/event-item. Foreign keys preserve provenance and existing staff/task identities. Constraints protect required-origin items, completion state, N/A reasons and closeout requiring an actual end.

The starter migration preserves an existing master; its reverse is intentionally non-destructive. There is no migration that resets participants, tasks, staff, events, expenses, compensation, offline records or deployment configuration. Legacy events receive no invented end timestamps or mass-generated closeout tasks.

A dedicated migration test upgrades pre-10D Event, StaffProfile and Task rows and compares every field before/after. It confirms historical events remain without guessed execution state and starter items remain optional. This ran only in an isolated SQLite test database. PostgreSQL locking/concurrency and physical trailer/hardware verification are not claimed.

## 11. Functional Testing

The final combined suite ran **1,042 tests: 1,037 passed and 5 PostgreSQL-only tests skipped**, with no failures (106.099 seconds). The initial Event Workspace baseline ran 993 tests with the same five skips. A focused 47-test execution/UI/upgrade run also passed. Framework checks, migration drift checks, whitespace checks and all five frontend scripts passed. All 22 inventoried participant/operator runtime callback bodies matched the starting commit.

Verification is recorded in the accompanying completion report. It includes the full Django suite, focused execution/UI/migration tests, framework checks, migration drift checks, template compilation, whitespace checks and all five existing frontend scripts:

- `scripts/test_shell_focus_9g.cjs`
- `scripts/test_task_modal_9g2c.cjs`
- `scripts/test_notifications_9g2d.cjs`
- `scripts/test_ui_patterns_10c.cjs`
- `static/games/duck_hunt/logic.test.cjs`

Use `DJANGO_SETTINGS_MODULE=config.settings.test`, empty `DATABASE_URL`, `DB_ENGINE=sqlite`, an isolated `DB_NAME`, and `python manage.py test --noinput` for the existing SQLite test workflow. Never point tests or migration experiments at the operational database. Production-like PostgreSQL checks remain a separate limitation.

The final whole-template scan compiled 186 of 187 templates. `templates/events/offline_ticket.html` has an existing unquoted URL-tag syntax error, is byte-identical to the starting commit, and has no references in tracked application/config/template code. Current offline ticket views return PNG/PDF directly. That unused legacy file was preserved and documented; all changed/new templates compiled.

## 12. Development Workflow Continuity

Implementation remained in `/workspaces/btw-event-workspace-ui`. A new development branch carried the original dirty Event Workspace changes without resetting, cleaning or switching their files. The old `ui/event-workspace-completion` branch reference remains at the original checkpoint.

Twenty-five protected worktrees were compared using branch, HEAD, raw tracked/untracked status and content fingerprints; they matched the recorded baseline. The migration handoff, simulator/6B work, older dirty Render checkout and unrelated work were preserved.

Future work must inspect the actual branch/worktree and these documents, scope changes with the owner, implement on the approved development branch/worktree, run appropriate regressions and independent visual checks, review the exact diff/staged list, and commit/push only when authorized. Merge and Render deployment require separate authorization. Do not redesign or change development tools because a conversation/account changes.

## 13. Git Delivery

Delivery is one combined commit on `build-10d-operations-execution`, containing the inherited Event Workspace work, Build 10D source/tests/migrations and these documents. The final local completion report records its exact SHA and remote verification. Generated reports, screenshots, local manifests, runtime settings, databases and credentials are excluded from the application commit.

No merge, rebase, force-push, Render branch update or deployment is authorized by this build.

## 14. Outstanding Issues and Boundaries

- The unused legacy offline-ticket template syntax issue above predates this build.
- PostgreSQL-only tests are skipped in the available SQLite environment. Previous historical PostgreSQL issues are not claimed repaired by this build.
- Browser evidence uses synthetic data in the isolated private preview, rather than production/Render or physical kiosks/trailers. It is not hardware or full offline-journey certification.
- The actual kiosk preview/runtime and existing restricted Django Admin screens are intentional UI exceptions, not newly modernized engines/editors.
- Existing/imported events need explicit checklist initialization and manager assignment; historical actual end requires a known source.
- Complex readiness policies, safety certification, approval/evidence chains, notification delivery, future kiosk editing/static-admin previews and further roadmap implementation remain outside this build.
