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
