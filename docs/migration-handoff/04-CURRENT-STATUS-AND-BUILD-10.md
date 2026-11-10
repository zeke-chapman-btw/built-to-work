# Current status and later Build 10 planning

## Build 9F checkpoint

**Complete for the approved development/preview scope.** HEAD is 01e48c2bc0adcac0a5047291791cdda40cbe5328 on build-9f-event-reporting. Source, migrations, recorded acceptance and a fresh full regression support that conclusion. It is not a blanket production-readiness or hardware certification.

New ExpenseCategory, ManualExpense, IncentiveLine and CostReview records implement audited manual costs, arbitrary actually distributed items and source-aware reconciliation. Three migrations applied successfully during the preview deployment. Approved Workday pay/per diem and approved MileageTrip snapshots are queried directly rather than copied into expenses. Reopened/pending/void records do not count; trips count once. Category history and void evidence remain.

Recorded subtotal is separated from reconciled total; intentional zero requires review. Pending/missing source records block complete totals, and changed sources invalidate reconciliation. Cost KPIs need complete cost and a nonzero denominator. Event and cross-event reports derive distinct official participation and completion/score metrics from existing models. Four private formula-safe CSVs are provided. Existing 9D notes and shared portal components are reused.

## Verification ledger

| Evidence | Result / scope |
| --- | --- |
| Fresh handoff full regression | 678 tests in 38.207 seconds; OK (skipped=1), isolated in-memory SQLite with config.settings.test |
| Fresh Django system check | Passed, no issues |
| Fresh migration drift check | makemigrations --check --dry-run: no changes detected; no migrations applied to a working database |
| Fresh preview /health/ | status ok; liveness only |
| 9F delivery focused suite | 72 passed in 2.258 seconds |
| 9F delivery full suite | 678 run in 35.969 seconds; OK (skipped=1) |
| Populated 9E→9F migration at delivery | Hash preservation of all 96 existing tables, including approved Workday/MileageTrip and participant/operations records |
| Render delivery | dep-dbbbvv2vcj2c739m3ngg, Live; all 3 reporting migrations succeeded; SHA matches 9F |
| Git verification | HEAD/branch inspected; tracked source hashes compared before/after handoff; documentation left uncommitted |

The SQLite skip concerns PostgreSQL row-lock behavior. No PostgreSQL concurrency run, physical rig validation, real provider send, production deployment, real-data import, or fresh replay of every virtual recovery script was performed for this handoff. Those boundaries were deliberately left unchanged. Expected PermissionDenied logging in negative authorization tests is not a failed suite.

## Browser/deployment evidence from 9F acceptance

- Isolated manager/admin tests covered expense creation/correction/void, arbitrary incentives, category rename/deactivation, reconciliation and before/after audit history. Isolated approved mileage integration was exercised without changing preview policy.
- The live SYNTHETIC Build 9F preview verification event reconciled 125.50 operating cost + 200.00 approved pay + 40.00 per diem + 106.16 distributed incentives (13.27 × 8) = **471.66 USD**. The separate purchase reference was excluded, then voided for history verification. Source changes invalidated review; rereview restored the total.
- Pending Workday pay was excluded until approved. The category was renamed/deactivated while historical expense labels remained intact. No real payment occurred.
- Existing earlier synthetic activity produced 2 registrations, 1 kiosk completion, 1 simulator completion and 50% overall completion. No artificial participant activity was added merely to populate the new dashboard.
- All four CSV downloads were inspected. Empty scores/zero denominators and unknown finances remained unavailable instead of fabricated zeros.
- Desktop and 390px mobile layouts were checked, including no horizontal report overflow. Events, Operations, Participants, Admin, Settings, Workdays and staff-schedule entry pages loaded. The administrator's missing linked StaffProfile was correctly explained.

Retained live fixture: /reports/events/bde73057-d9c2-4c12-955c-a91da128c90a/. It is visibly synthetic, not verified actual expenditure. Review it read-only; do not delete or treat it as real cost history. Prior screenshots and delivery notes supplement source evidence but are not a new production audit.

## Known limitations / technical debt

1. Real operational document uploads remain disabled until durable private storage is approved/configured and verified. Synthetic PDF demos are not uploads.
2. The actual mileage rate/effective date was not supplied; it remains unconfigured at 9E delivery. True per-mile calculation is implemented; do not import Base44 tiers or guess a rate.
3. Home Updates and Analytics are visual previews, not live feeds/counters. Existing task management is functional in Operations/event pages, but Home has no task panel.
4. Per-employee allowed compensation roles were requested historically but are absent from StaffProfile and CheckinForm. Active role selection is currently global. Set-Up Only was discussed; no verified initial rate was imported for it. These are unresolved product gaps, not authorization to change compensation now.
5. Some report score displays show excessive decimal precision. The report/service/template implementation is compact; readability/organization can be improved only within an approved future scope.
6. Event-list date filters historically use UTC overlap semantics, while 9F comparison uses local event start dates. This is documented behavior, not interchangeable filters.
7. Cross-event participant totals are participant-event pairs, not globally unique people. Financial reconciliation cannot automatically detect whether two independently entered descriptions refer to the same real-world purchase.
8. No complete disconnected synchronization for 8–9F domain additions, Windows/hardware certification, real communications-provider acceptance, or production readiness is implied.
9. Requirements use ranges rather than a frozen dependency lock; observed Python 3.14.2 differs from requested 3.14.3. Document/recheck, do not upgrade as a migration side effect.
10. Preserve separate uncommitted simulator/UI worktrees and the separate 6B XML branch. Do not silently merge their conflicting older/current helper strategies.

## Build 10 — deferred, not started

**Latest owner steering during this handoff:** “We will work on 10 later as well.” Therefore Build 10 is future work; its exact scope is not a prerequisite for accepting this documentation. Prepare a separate approved brief later. Do not start it from this file.

Recovered user messages in “Confirm Staff Deadline Inclusion” asked to make Quick Actions smaller/more columns so tasks fit without major scrolling, asked whether the existing small Home analytics area should stay, and proposed four areas: **Tasks, Quick Actions, Updates, Analytics**. These establish concerns and a possible layout, not a finalized four-quadrant acceptance design. The exact earlier “What is build 10?” assistant response was inaccessible as a content-reference placeholder.

The present handoff request also names a later **visual UI/UX audit against the existing Base44 event-management app**. Preserve this as a requested future planning activity. Earlier briefs called the comprehensive UI pass “Build 9G”; the current owner also refers to Build 10. Their numbering/relationship needs confirmation when planning resumes. Do not infer that a build was skipped, completed, renamed, or authorized.

Future discussion should cover: composing existing tasks into Home rather than building another task engine; action density/responsive order; useful role-scoped Updates and analytics sources; read/unread meaning; quick-action defaults; empty/loading/error states; accessibility and mobile priorities. A four-part layout, notification transport, analytics customization, task reminders, and revised check-in placement remain design questions unless later explicitly approved.

When resumed, inspect the current portal and authenticated Base44 read-only, compare actual workflows and visual organization, propose concrete changes, and request approval before code. Reuse current models, permission boundaries, six-section navigation, Settings gear, tokens/fonts and established testing workflow. No hosting/toolchain change follows from moving ChatGPT accounts.
