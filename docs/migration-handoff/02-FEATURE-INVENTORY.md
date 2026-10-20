# Feature inventory at the 9F checkpoint

Status vocabulary: **Implemented and verified** means source was inspected and relevant automated/session acceptance evidence exists; it does not certify production or physical hardware. **Implemented but unverified** means code exists without current end-to-end evidence for the stated boundary. **Partial** means a working subset exists with explicit gaps. **Planned** records requirements only. **Not found** means no implementation was located in the inspected 9F tracked source. The fresh full Django suite ran 678 tests successfully with one skip; not every feature was re-exercised manually during this documentation task.

## Customer/employer management and recruiting

| Feature | Status | Evidence / boundary |
| --- | --- | --- |
| Customers navigation | Implemented and verified as a planned placeholder | apps/internal_ui/navigation.py explicitly says no customer records/tools available; template/navigation tests |
| Customer/employer CRUD, contacts, customer login | Not found | No customer/employer app/models/routes found in active tracked source |
| Open positions/campaigns, candidate matching/bundles, employer access grants | Planned; not found as implemented workflows | Product discussion and future HubSpot direction; no recruiting/campaign models located |
| Anonymized/partial/full candidate sharing | Planned | Must be permission and privacy designed; no customer sharing capability inferred from internal profiles |

## Events and scheduling

| Feature | Status | Evidence / boundary |
| --- | --- | --- |
| Event creation, logistics, local times/DST, lifecycle, draft/publish/finalize/archive | Implemented and verified | apps/events/models.py, event_management.py, views.py, forms/templates; apps/events/test_event_management_9b.py; DOCS/BUILD_9B_EVENT_MANAGEMENT.md |
| Event search/status/type/date filters and overview/readiness | Implemented and verified | apps/events/views.py; templates/events/; 9B/8E-D tests |
| Public registration publication, groups, capacity, event questions | Implemented and verified | apps/events/public_views.py, services and public registration tests; public registration remains separate from Publish to Staff |
| Automatic completion, recurring overnight shift engine, general calendar integration | Not found / outside approved scope | Current is derived; no silent lifecycle completion; daily staffing times are same-local-day |

## Staff, assignments, availability and attendance

| Feature | Status | Evidence / boundary |
| --- | --- | --- |
| Staff profiles, roster-only identities, explicit user links | Implemented and verified | apps/staffing/models.py, services.py, views.py, tests.py; DOCS/BUILD_9C_STAFFING.md |
| Published opportunities, sign-up deadline, availability requests, approve/decline, direct assignments | Implemented and verified | Same source; approval immediately confirms assignment; self-approval prohibited |
| Daily staffing needs, conflicts, schedule/history, immutable revisions | Implemented and verified | StaffingPlan/DailyStaffingNeed/StaffAssignment/AssignmentDay; 68-test 9C evidence |
| Staff daily check-in, own history, manager corrections/approvals/voids | Implemented and verified | apps/workdays/; 65-test 9E evidence; fresh regression covers current code |
| Daily roles, effective-dated pay/per diem, individual mileage | Implemented and verified; actual mileage configuration incomplete | True per-mile trip ledger, snapshot amounts; numeric approved mileage rate intentionally unconfigured at delivery |
| Per-employee allowed role assignments | Previously requested; not found | CheckinForm offers active StaffRole rows; StaffProfile has no allowed-role relation. Do not claim this restriction exists |
| Home-based staff check-in entry | Previously requested / future placement | Current workflow is event/assigned-day based; Home integration not implemented |
| Participant arrival/check-in/checkout workflow | Removed / not active | Legacy events.Attendance data/model retained; activity QR scan is not staff attendance/check-in. Build 7 closure forbids restoring old workflow by accident |

## Tasks, notifications and Home

| Feature | Status | Evidence / boundary |
| --- | --- | --- |
| Standalone/event task CRUD, assignee, due date, priority, status, filters/overdue | Implemented and verified | apps/operations/models.py, services.py, views.py, tests.py; DOCS/BUILD_9D_OPERATIONS.md |
| Staff own-task view and limited status transitions | Implemented and verified | /operations/my-tasks/; task access scoped to active linked profile |
| Event notes and visibility/audit history | Implemented and verified | EventNote; existing notes reused in 9F, no competing notes system |
| Real operational uploads | Partial: adapter tested; deployment disabled | apps/operations/storage.py; durable storage prerequisite unmet. Fixed synthetic PDF demonstrations are functional |
| Immediate ticket delivery and operational incident queues | Partial: backend implemented/verified, real providers unverified | apps/events/communications.py, operations_views.py, deliver_communications command; consent/opt-in/idempotency guards |
| Task-assignment email/SMS or general notification center | Not found | 9D does not add a task notification backend; operational alerts are a different feature |
| Home layout and personal Quick Actions | Implemented and verified | apps/internal_ui/dashboard.py, views.py, forms.py; User.quick_action_ids; DOCS/HOME_DASHBOARD_PHASE2A.md |
| Home Updates feed and analytics values | Partial visual preview only | templates/internal/dashboard.html explicitly says updates/data not connected; analytics tiles show unavailable marks |
| Home task panel / four-quadrant design | Planned | No Home task query/panel in active template/view; design must be approved |

## Participants, registration and QR tickets

| Feature | Status | Evidence / boundary |
| --- | --- | --- |
| Canonical participant identity/career profile, smart intake, versioned forms | Implemented and verified | apps/participants/models.py, registration services/views, normalization, form-builder tests |
| Public/on-site/offline registration and consent evidence | Implemented and verified in software/virtual tests | registration/public/offline services; approved real consent policy/provider/hardware launch checks remain |
| QR issuance, ten-digit ticket allocation, reissue/recovery/print/image/PDF | Implemented and verified | apps/events ticket services/renderers/views; one PostgreSQL-only locking test skipped in SQLite |
| Participant account request, verification/login/reset/profile/email change foundation | Implemented and verified in automated coverage; live deliverability unverified | apps/participants/views.py, auth.py, urls.py; does not imply full participant portal |
| Internal participant directory/profile and review history | Implemented and verified | staff_directory_views.py, participant_review_views.py, participant_review_models.py, Build 8E-B/C/D tests |
| Qualifications, private documents, verification approvals | Implemented and verified for defined software scope | qualification*, participant_document*, verification* modules; evidence provenance, replacement/archive/reverification |
| Historical import/export and review-safe matching | Implemented and verified in synthetic tests | historical.py, historical_views.py; DOCS/BUILD_7D_HISTORICAL_DATA.md; real authorized source files not certified |
| Full self-service results/skills/certification/opportunity portal | Planned | Later participant-portal scope, not completed by account foundation |

## Kiosk, games and simulators

| Feature | Status | Evidence / boundary |
| --- | --- | --- |
| Stations, event context, staged official/test/demo experiences | Implemented and verified | apps/stations/; event readiness and 8E-D integration |
| Kiosk quiz, categories/pools/published question sets, timers/results/recovery | Implemented and verified in software | apps/assessments/; static/kiosk/; docs/KIOSK_DESIGN_STANDARD.md; real touchscreen/scanner acceptance remains separate |
| Configurable game framework and playable Duck Hunt | Implemented and verified in software | apps/games/; static/games/duck_hunt/; DOCS/GAME_FRAMEWORK.md; hardware lightgun calibration not certified |
| Simulator receiving API, review/replacement and accepted official result progression | Implemented and verified in 9F ancestry | apps/simulators/; docs/SIMULATOR_HELPER_CONTRACT.md; future/current physical sender must honor server contract |
| Windows simulator XML monitor | Implemented but unverified here; separate branch | origin/build-6b-simulator-results at 2b4ae2b705825d6dbe16a3b4728387d163a48672 is not an ancestor of 9F. Branch docs describe local XML archive/index, no server delivery, scores Pending Formula |
| Older OCR helper/calibration importer | Separate uncommitted work; not current 9F functionality | /workspaces/built-to-work has untracked helper and importer files. Later 6B branch changes normal workflow to XML; do not silently merge either |
| Physical SimU, Windows startup, Sinden/scanner/printer/network readiness | Unverified by Codespace tests | Requires approved device validation; code/virtual success is not hardware certification |

## Analytics, integrations and administration

| Feature | Status | Evidence / boundary |
| --- | --- | --- |
| Event accepted activity overview and readiness | Implemented and verified | apps/events/experience_operations.py; 8E-D integration; live earlier synthetic records |
| Manual expenses, arbitrary distributed incentives, approved staff cost, reconciliation | Implemented and verified | apps/event_reports/; 72 focused tests and deployed acceptance |
| Event metrics/KPIs, filtered cross-event totals and weighted ratios | Implemented and verified | reporting.py; current verified source semantics in DOCS/BUILD_9F_EVENT_REPORTING.md |
| Four private formula-safe report CSVs | Implemented and verified | views.py; all four downloaded and inspected in 9F acceptance |
| MoZeus connector / automatic external analytics feed | Not found | No connector found. Latest owner decision is to move away from MoZeus; no further reconstruction required |
| HubSpot integration / automated post-event campaigns | Planned, not implemented | No HubSpot source/app/client found. Recovered product discussion describes two-way profile/intake sync and aligned fields |
| Offline central/trailer sync, preparation, reconciliation, SQLite encrypted recovery | Implemented and virtually verified; limited domain coverage | apps/events sync/downsync/backup services and scripts/build_7*virtual_integration.py; no general replication of 8–9F data |
| Global categories and compensation settings; permissions and audits | Implemented and verified | event_reports category UI, workdays compensation UI, Django user/group administration; no automatic broad grants |

No production-readiness claim is made. See 04 for fresh vs historical evidence and 05 for integration-specific blockers.
