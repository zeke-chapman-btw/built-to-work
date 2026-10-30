# Build 13 — local verification and owner review

This is an uncommitted candidate on `build-13-customer-management`, rooted at
`/workspaces/btw-build-13-customers`. Starting HEAD is
`21d8e70ddd7c8d23cfaff2679a73d5ae9e46b608` from the accepted Preview branch.
Nothing in this build authorizes a commit, push, integration, deployment, live
account change or shared database migration.

Read `BUILD_13_CONTRACTS.md` for the permission map, submission matrix, seat and
Primary Lead rules, revision classification, lifecycle and Campaign contracts.

## Implemented surfaces

- `/internal/customers/`: searched/filtered/paginated directory with real primary
  row links and separate edit controls; validated return context survives nested tools.
- Eight customer tabs: Overview, Company Profile, Workforce Needs, Campaigns,
  Matches, Users & Contacts, Access & Plan, History.
- Company, branch/location, business-contact and explicit relationship editors;
  archive/restore preserves children and connected organizations.
- Prepared identities, explicit multi-company memberships, reversible membership
  states, configured allowances and atomic Primary Lead replacement.
- Typed hiring drafts, mandatory-topic submission validation, explicit review,
  actual-version approval, pending/effective revisions, administrative corrections,
  manual closure, immutable snapshots and contextual history.
- Campaign planning, explicit approval/activation, version invalidation on planning
  or association changes, same-company Need associations and global Campaigns.
- Searchable paginated History backed by the existing AuditLog and an indexed scope.

## Verification commands

Use the existing Python runtime; do not install or change dependencies:

```sh
DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_URL='' DB_ENGINE=sqlite DB_NAME=:memory: PYTHONDONTWRITEBYTECODE=1 /workspaces/built-to-work-build-8a/.venv/bin/python manage.py test --noinput -v 1
```

The complete suite has 1,650 tests: 1,625 passed, 25 PostgreSQL-only checks skipped
on SQLite, no failures. Customer/shared-navigation coverage includes the previously
run focused regression, with the new suspended-membership allowance edge case also
covered in the complete suite. The original dashboard query
budget is retained and passes; it was not relaxed.

Dedicated PostgreSQL 17 container: `btw-build13-tests`, owned by this task and bound
to loopback. Private runtime credentials are outside the repository. With its
private environment loaded, run:

```sh
python manage.py test apps.customers --noinput -v 1
python manage.py test apps.accounts.test_staff_access_postgres apps.events.test_ticket_workflow_7c2 apps.operations.test_operations_postgres_10c apps.participants.test_portal_preview apps.participants.test_portal_concurrency_12 apps.participants.test_portal_profile_expansion apps.participants.test_intake_concurrency_11b --noinput -v 1
```

Customer PostgreSQL suite: 89 passed, no skips/failures. It includes six real
two-connection races: last seat, Primary Lead transfers, cross-actor/cross-company
identity provisioning, stale policy updates, competing hiring approvals and
Campaign reassignment. Backend process IDs are asserted distinct. Existing PostgreSQL regression subset: an earlier 70-test run passed; the later
combined 159-test acceptance run had 158 passes and one error in the unchanged
TicketAllocationConcurrencyTests.test_parallel_allocations_are_unique. No skips.
Cold simultaneous creation of the cloud allocation block can raise uniqueness
ValidationError from model full_clean before get_or_create can retry. The unchanged
21d8e70 baseline reproduced this in 25 four-connection trials (7 errors). Event
ticketing.py and models.py are byte-identical to that baseline. The existing thread
test also left four database sessions open, causing its database teardown error.
No ticketing application repair or test weakening was included in Build 13.
These are targeted subsets, not an all-tests PostgreSQL run; this known existing
ticket issue remains for separately authorized correction/release review.

Frontend command (16 passed, no skips/failures):

```sh
node --test scripts/test_event_navigation_pass2.cjs scripts/test_notifications_9g2d.cjs scripts/test_shell_focus_9g.cjs scripts/test_task_modal_9g2c.cjs scripts/test_ui_patterns_10c.cjs static/games/duck_hunt/logic.test.cjs
python manage.py check
python manage.py makemigrations --check --dry-run
```

System and drift checks are clean. Static collection produced 194 files into an
isolated task-owned directory. Additive upgrades on fresh SQLite and PostgreSQL
databases first applied the accepted non-customer migration graph, seeded an
existing synthetic user and AuditLog, then applied Customers. UUID, credential
fields, user values and existing audit values remained unchanged.

## Browser review

Private local review URL:
`https://symmetrical-funicular-qvpxx6rx5j7wc96wv-8040.app.github.dev/internal/customers/`

This is the isolated candidate and synthetic SQLite database, not Render. The
Codex browser has a synthetic Administrator session. No credentials belong in
this document. Codespace access and a running local server are required.

Review SYNTHETIC Atlas Fabrication for prepared memberships, a Primary Lead,
effective/pending hiring revisions, contacts and Campaigns. SYNTHETIC New Prospect
demonstrates unconfigured allowances. Additional synthetic directory rows exercise
pagination. Browser-created synthetic records are disposable review evidence.

Desktop 1440×1000 and phone 390×844 / 360×844: all eight tabs inspected with saved
screenshots and live DOM measurements. Ordinary content is 920px at desktop;
cards use 24px desktop / 16px phone padding, phone outer gutters are 16px, tab and
form controls have 44px minimum targets. Tabs scroll within their own navigation;
the page has no horizontal overflow. Identity typography is Jost 20px/28px, 600.

Review included customer create/edit/archive/restore, allowance configuration,
Primary Lead capacity error and explicit seat-neutral swap, hiring draft errors
and preserved input, pending/effective terms, actual-version approval, Campaign
planning/association/approval, history and keyboard navigation. Chrome had stale
or clipped captures; the Codex browser supplied reliable replacement evidence.
Automated/keyboard checks do not constitute screen-reader or other real assistive
technology certification.

## Test maintenance and protections

Five existing test files update obsolete placeholder/navigation expectations for
Customers/Campaigns. They now assert the real directory and the narrower role
boundary. Global Approvals stays planned. Existing permission, financial, event,
task and portal assertions remain; query-count limits are unchanged.

One existing account test fixture was also made calendar-stable: assignment and
check-in use the same captured event-local clock. Three midnight errors reproduced
on the exact unchanged baseline before this test-only correction. Workday rules,
permission assertions, dates on application records and application services were
not changed.

All 62 pre-existing worktrees, tracked/index/untracked content and existing branch
references were compared with their initial snapshot. No original worktree or
remote branch changed. The only new local branch is this Build 13 candidate at
its starting HEAD. No staging, commit, push, merge or deployment occurred.

## Deferred functionality

Build 14 customer portal activation, invitations and Lead-managed Members remain
unimplemented. All Leads (including additional Leads) may manage Members within
their organization's configured limits in that future build; only BTW staff may
create/designate Leads, replace Primary Leads or change allowances/entitlements.

Build 15 matching/recruiting, candidate disclosure, exclusivity and pipelines are
not implemented. Entitlements cannot turn those on. Individual directory-search
user allowances await an approved individual permission/counting model. Global
Approvals, customer billing, future task integration and automatic closure are
not introduced.

The coarse existing staff-security lock deliberately serializes customer writes
across organizations; see the contract for the integrity rationale and future
scaling consideration. Operational policy and authentication safeguards are not
replaced by customer entitlements.

## Later Preview release checklist — do not execute under this authorization

1. Obtain owner acceptance and explicit commit/push/Preview release authorization.
   Review the reproduced existing ticket allocation race before a later release;
   any application correction requires its own authorized scope.
2. Recheck the candidate manifest/fingerprints, protected state and actual current
   Preview SHA. Preserve newer approved changes and stop on material conflicts.
3. Stage only the reviewed Build 13 files and commit on this dedicated branch.
4. Prepare a traceable isolated integration; run applicable tests/checks against
   the exact integrated source and confirm additive PostgreSQL migration compatibility.
5. Review the version-2 Manager role configuration difference before applying it
   to Preview. It adds only `customers.manage_customers`; do not enroll users or
   change existing memberships. No live role configuration was applied here.
6. With separate deployment approval, push only the accepted Preview integration
   and monitor the established automatic Render deployment.
7. Verify actual deployed SHA, migrations, health, staff login, Admin/Manager access,
   denied roles, Customers/Campaigns, existing workspaces and portal/intake isolation.

Production remains outside this build and release checklist's authorization.
