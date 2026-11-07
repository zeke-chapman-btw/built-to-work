# Pass 1 — shared navigation, fields, filters and expense actions

Implemented original audit findings UX-004, UX-008, UX-010 and UX-012. UX-012 is the expense issue, called UX-011 in the authorization brief; original audit identifiers remain authoritative.

Customer secondary navigation now uses one server-side mapping. Exact destinations use `aria-current="page"`; nested position and campaign routes retain `aria-current="location"` on their parent. Existing Staff and Participant navigation remains in use.

Workspace fields share clear borders, explicit desktop sizing and 16px text/44px minimum height below 768px. Focus, error and disabled states retain distinct tokens. Expense filters have distinct control IDs from their simultaneously open editor, so Category labels name the correct control.

Reports use a native Filters disclosure with an active count and visible selected scope. Phones start compact; desktop starts expanded. The existing GET form, hidden drilldowns, validation, export URLs and tab state are preserved. Invalid filters open automatically. Escape closes the disclosure and focuses its summary.

Shared record actions now sit in normal flow. The original full-page expense form reproduced field overlap at 1440, 768, 390 and 320px; corrected forms do not. Add and Edit drawers also have no measured field overlap at those widths.

## Verification

- PostgreSQL: 136 tests run, 135 passed, one existing expected skip, no failures.
- SQLite: 136 tests run, all passed, no skips or failures.
- Exact labels: `apps.customers.test_cleanup_customer_navigation`, `apps.customers.test_portal_refinement_p1`, `apps.reporting.test_cleanup_reporting`, `apps.reporting.test_workflows`, `apps.event_reports.test_cleanup_expense_controls`, `apps.event_reports.tests`, `apps.event_reports.test_expense_workflow`, `apps.event_reports.test_compact_editor`.
- Durable logs: remote state `pass1-pg-gates.log`, `pass1-sqlite-gates.log`, `pass1-gates-results.json`. Earlier failed discovery/session setup runs are retained and superseded, not omitted.
- Browser: normal sign-in as separate synthetic Staff administrator, Customer Lead and Participant accounts. All three measured at 1440, 768, 390 and 320px; no page-wide overflow.
- Report GET submission retains `State / region: GA`; mobile stays compact; Escape returns focus to SUMMARY.
- Expense Add: whitespace server validation, amount retained, corrected red border, successful isolated $10.00 save, cancellation back to Expenses.
- Expense Edit: required reason validated, successful synthetic note edit retains $10.00, description-to-category Tab order, correctly named Category, Escape dismisses drawer.
- `git diff --check` passed.

These are focused Pass 1 suites, not a claim of full platform regression coverage. Full regression remains a later release gate.

## Evidence

Measured metadata is in `tests/pass1-*-browser.json`, `tests/pass1-before-*.json` and `tests/pass1-interaction-checks.json`. Matched report and full-page expense pictures are under `before/pass1-*` and `after/pass1-*`. Staff drawer, Customer and Participant images are under `after/pass1-*`. Original audit screenshot references remain in `FINDINGS-CLOSURE.json`.

Physical phone/soft-keyboard and full screen-reader checks are unavailable in this environment. Actual 200% browser zoom remains pending; viewport evidence is not presented as zoom evidence. No real messages, financial approvals, device commands or Production actions occurred. All review writes use the fresh synthetic local PostgreSQL database.


# Pass 2 — Customer clarity, historical importer and game administration

Implemented original audit findings UX-002, UX-003, UX-005, UX-006, UX-007 and UX-011. The authorization called the game-administration issue UX-012; this package preserves the original audit identifiers.

Customer lists, editors, actions, membership labels and activity now consistently call the record a Position. Workforce Requests and Campaigns keep their separate meanings. Activity labels are presentation mappings: stored audit actions, IDs and timestamps are unchanged.

Position editors show one short prerequisite status. Full submission rules, company/contact details, current missing topics and access explanations remain in a disclosure. All seven existing groups, section navigation, bound fields, draft saving and unsaved-change protection remain. Missing submission topics now retain their field labels instead of repeating an unnamed error. In the measured 320px case, the first field moved from about 759px to 607px from the top.

Only the dashboard uses a wider frame. Desktop content grew from 920px to about 1153px, with independent Action required/Candidates and Quick actions/Recent activity panels. Phones retain one column. Existing counts, data scope and links are unchanged.

The current unified participant directory has an Import participants action gated by the existing import permission. The existing importer provides format/size/row/column guidance, required-name guidance, supported optional fields, mapping/duplicate-review/history explanations and a synthetic sample CSV available before a batch is created. No parser, matching, consent, registration, ticket, provenance or saved-mapping behavior was replaced.

Games explicitly identifies advanced maintenance as restricted administration. Game administration list and edit pages provide a 44px Operations return link. Native permissions and maintenance forms remain in use.

## Verification

- Broader PostgreSQL: 277 tests passed, no skips or failures.
- Broader SQLite: 277 run, 267 passed, 10 backend-specific skips, no failures.
- Final focused checks after submission/activity-label corrections: 18 passed on each database, no failures. These overlap the broader suite; they are not 295 unique tests.
- Exact broader scope: 19 modules, listed in remote state `pass2-gate-labels.json`. Logs: `pass2-{pg,sqlite}-gates.log`, `pass2-{pg,sqlite}-final-focused.log`; durable result JSON accompanies them. Earlier failed directory-discovery/setup attempts are retained and superseded.
- Browser: measured 1440/768/390/320px for Customer dashboard/editor, unified directory, importer guide, game workspace and native game list/edit pages. No page-wide overflow in these captures. The new return link was clicked successfully at 320px.
- Separate Customer Lead/Member sign-ins. Lead can create a position; Member sees no management action and receives 403 on the create endpoint. Both receive 403 on the staff importer.
- An incomplete synthetic draft saves; its separate submission is rejected with named missing topics. The working revision remains draft and no effective revision is created.
- The sample completed the unchanged upload → map → Ready preview → import workflow in the isolated synthetic database. Source SHA256 matches the delivered CSV. Exactly one historical record was created for its synthetic participant; zero event registrations, consent acceptances or participant accounts were created.
- The browser file-transfer tool stalled for 3754.7 seconds before completing. No further file-transfer attempts are needed; this is recorded as a tooling limitation, not an application failure.
- `git diff --check` and guarded source-hash verification passed before tests/checkpoint.

## Evidence and limits

Before/after JPEGs are under `before/pass2-*` and `after/pass2-*`; measured metadata and interaction/provenance checks are in `tests/pass2-*.json`. Original audit screenshot references remain in the finding register. Screenshot encoding was checked and filenames normalized to `.jpg` without changing pixel data.

Physical phone/soft-keyboard and full screen-reader checks remain unavailable. Native Ctrl+plus did not alter the measured viewport or pixel ratio; actual 200% zoom remains unverified. Responsive viewport checks are not described as zoom checks. These focused suites are not a claim of full platform regression coverage. No Production deployment, live financial action, real communication, real historical backfill or device command occurred.


# Pass 3 — Reports, payroll and administration

Implemented on ux-platform-cleanup-round1 from b765b2b84e545cc84067743982edfa7270a4f03a. This closes original audit IDs UX-001, UX-009, UX-013 and UX-014; the authorization's swapped topic/ID references were reconciled against the authoritative original findings.

## Changes and preservation

- UX-001: Highlights now presents eight key metrics first, with all eight additional metrics and financial context in a named disclosure. Two primary charts precede nine additional trends. All sixteen metric values and their original definitions remain in the DOM; CSV uses the original complete projection. Each summary card has a native About this metric disclosure. All disclosures expand for browser printing and restore afterward. Missing amounts remain Unavailable, never zero. Narrow financial cards use a readable single column. Other six tabs retain their calculations, charts, tables and authorized outputs.
- UX-009: Live operational reports link to the versioned event report when existing permissions allow it. The versioned view identifies current operational sources separately from draft calculations and frozen finalized versions, and returns to the same event. Historical v1 remains read only and retains version/export labels.
- UX-013: The staff account form groups existing fields under Account identity, Roles and staff profile, Exceptional financial authority, and Reason and confirmation. Ordinary roles and explicit finalizer/payroll-approver designations remain independent. No fields, defaults, permissions or validation rules were changed. Other account action forms retain the existing fallback rendering.
- UX-014: Add/Edit manual business figures display the existing event name, period and time zone; Edit also names the figure. Save, Cancel and Back return to the same event's Manual business figures section. Source notes and attributable-business-only guidance remain. Calculated lead, score, qualification and cost totals cannot be replaced.

No reporting/payroll service, model, migration, financial formula, approval authority, recruiting rule, export implementation or historical record was changed.

## Verification

- Final focused tests: PostgreSQL12/12 pass; SQLite12/12 pass, no skips or failures. Eight reporting presentation tests (including the three existing compact-filter tests) and four staff-grouping tests. S/pass3-{pg,sqlite}-focused-final2.log and pass3-focused-final2-results.json.
- Broader17-module selection: PostgreSQL290 run,289 pass,1 expected skip; SQLite290 run,283 pass,7 expected skips; no failures/errors. Exact labels and outcomes: S/pass3-gate-labels.json, pass3-{pg,sqlite}-gates.log, pass3-gates-results.json; local tests/pass3-gate-summary.json. These subsets overlap earlier tests and are not a full regression total.
- Node reporting UI harness5 cases pass: responsive Filters/error handling, nested disclosure Escape/focus, print expansion/restoration including repeated beforeprint, and the Print action. Node syntax and git diff whitespace checks pass.
- Django system checks on both databases, migration drift --check --dry-run and applied synthetic review migrations all pass. No new migrations.
- Full regression is separately running against proper config.settings.test defaults:167 explicit modules,2259 distinct IDs per database, PostgreSQL whole-suite and SQLite7 exact-coverage partitions. Do not treat this Pass3 report as proof that the full run finished.
- Native Codex browser: all seven tabs at measured1440/768/390/320; table-local horizontal scrolling, no page-wide overflow. Captures are linked through tests/pass3-stable-{before,after}-browser.json. Final captures flush the compositor frame after viewport changes; earlier capture attempts remain separate.
- Enter opens metric/context disclosures; Escape closes the active disclosure and restores summary focus. Native required validation focuses Reason. A whitespace Reason reaches server validation with event context retained; subsequent synthetic Save succeeds and retains12items. Save/Cancel return to #manual-business-figures. No financial totals changed.
- Staff Create/Edit at all four widths: every applicable field appears once; Create financial authority flags unchecked; Edit retains EventStaff + account-active and no exceptional flags. Native confirmation requirement and server required-field errors checked. Cancel returned to the correct account/list. No new account or authority was granted.
- Supplemental isolated read-only checks: eight roles use the matching server realm backend. Manager can administer ordinary staff but cannot finalize/approve without explicit designation; Recruiter/EventStaff/Customer/Participant boundaries retained. Another contractor's statement403. See tests/final-readonly-verification.json.
- Nine finalized outputs succeeded: event/payroll/own-statement × CSV/XLSX/PDF. CSV/XLSX rows and extracted PDF text retain reporting period, status/version and USD metadata; private no-store headers present. Existing event export periods use explicit UTC offsets, while UI uses America/New_York. No new recipient template was introduced.
- Frozen eventv1 and payrollv1 content hashes verify after the synthetic manual edit. Contractor UI shows100USD compensation +20USD reimbursements =120USD reported; approval is not payment confirmation. All such records exist only in the freshly seeded isolated review DB, never Preview.

## Evidence and limits

Matched before/after routes: Highlights, operational report, live/frozen versioned report, manual Add/Edit, staff Create; all four widths. Staff Edit, all six additional tabs, approved payroll and own contractor statement also captured. Manager Tasks/Approvals/Participants/Customers/Operations, Recruiter, EventStaff and Participant profile/preferences/events/jobs/activity were reviewed. All available role sessions use synthetic identities.

Original audit screenshot references remain in reports/FINDINGS-CLOSURE.json. The original audit package is untouched. Local images are JPEGs with .jpg suffixes.

Actual200% browser zoom could not be demonstrated (native zoom keys did not change measured viewport or ratio); physical phone/soft keyboard, full screen-reader and actual print-dialog operation remain unverified. These are verification limits, not claims of coverage. Browser print event behavior is covered by the Node harness; existing PDF outputs were independently generated and parsed. No live messages, financial approvals, payments, invitations or device commands were performed.
