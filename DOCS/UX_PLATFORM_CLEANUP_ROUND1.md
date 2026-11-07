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
