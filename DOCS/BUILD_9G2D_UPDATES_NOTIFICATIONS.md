# Build 9G2-C Deployment & Build 9G2-D Implementation and Testing Report

Verified 20 October 2026. Repository: zeke-chapman-btw/built-to-work.

## Part A — 9G2-C Preservation and Deployment

### Exact revisions and safety gate

- Previous remote render-preview: e2a4fa2a2f7eb7beaca14a7a700028c4c0db69d0 (9G2-B).
- Approved C revision: 21ab57bb24658d8f4f55bb0fe7d14e99e37cd836.
- C branch: build-9g2c-tasks-workspace; worktree /workspaces/btw-build-9g2c.
- C was clean and one commit directly above B, containing 37 reviewed changed files. No C migrations, configuration or dependency changes.
- Remote references were fetched. Repository remote, ancestry, clean worktree and fast-forward path were verified before pushes.
- Normal pushes preserved C on GitHub and advanced remote render-preview to exact C. No merge, force push, rebase or checkout reset occurred. The old dirty local preview checkout was not advanced or cleaned.
- Both GitHub branch heads were checked after the pushes and again before D delivery.

[Preserved C source](https://github.com/zeke-chapman-btw/built-to-work/tree/build-9g2c-tasks-workspace)

### Render verification

- Intended disposable service: btw-preview, srv-db8n044s728c73aaet00; no production service targeted.
- Actual Render settings verified: GitHub render-preview branch, auto deploy On Commit, build ./build.sh, pre-deploy python manage.py migrate --noinput, existing Gunicorn start command, health path /health/.
- Existing synthetic-only preview documentation agreed with the owner's disposable-data statement; no contrary evidence was found. This was not a complete database-content audit.
- Automatic deployment: dep-dbbsul8473hc73b74i90. Full commit link matched approved C.
- Build succeeded 20:30:51 UTC; migrations reported No migrations to apply at 20:31:26; pre-deploy completed 20:31:29; Gunicorn/worker started 20:31:59; service Live 20:32:09.
- Available logs showed no new critical deployment errors. Public /health/ returned HTTP 200 with status ok, including a later independent workstation check during the Chrome-control failure.
- No manual deployment or live database migration was triggered; existing automatic deployment handled the update.

[Render deployment](https://dashboard.render.com/web/srv-db8n044s728c73aaet00/deploys/dep-dbbsul8473hc73b74i90)

### Actual authenticated C browser checks

Regular Chrome, using the existing authorized preview login:

- Tasks workspace and My Tasks, Assigned by Me, All Tasks loaded. Empty My Tasks was appropriate for this login's assignments.
- Combined status, priority and search filters returned the synthetic task; Archived correctly returned no matching records; returning to All retained filters.
- Floating Add Task opened the modal; standalone quick creation succeeded with disposable synthetic data.
- Event-context creation retained the primary Event and selected Participant link; Participant-context creation retained its Participant association.
- Existing B task details, linked records, comments and activity remained accessible; no changes were made to that historical task.
- Desktop and 390 x 844 mobile layouts were reviewed. Mobile document client/scroll widths both 375; dialog client/scroll widths both 324.
- Modal forward/backward Tab wrapping, Escape dismissal and focus return were checked.
- Three explicitly synthetic verification tasks were created in disposable Preview; no real personnel/customer data was introduced.

C deployment gate passed before D worktree creation. Render continues to run C, not D.

## Part B — 9G2-D Implementation

### Scope and development isolation

Branch: build-9g2d-updates-notifications. Worktree: /workspaces/btw-build-9g2d.
Baseline: exact C 21ab57bb24658d8f4f55bb0fe7d14e99e37cd836.
The focused local commit containing this document is identified by git log -1 in this worktree and the delivered local HTML report. D is not pushed, merged or deployed.

### Notification events and recipients

- New Task audit events drive in-app notices through an OperationsConfig receiver. Existing task/comment services and their AuditLog behavior remain authoritative.
- Assignment notifies the assigned active staff login, excluding the assigning actor. Reassignment notifies the current assignee and creator, excluding the actor.
- Comment creation, edits and moderation; completed/reopened/other status changes; archive/restore; and actual priority/due-date changes notify current assignee and creator, excluding the actor and deduplicating recipients.
- No-op saves, unchanged assignments/comments, title/description-only edits and linked-record-only edits do not produce notification noise.
- Administrators are not broadcast recipients merely because they can manage all tasks.
- Actorless historical imports/audit fixtures and raw fixture loads are not backfilled into notifications.
- A unique source-event/recipient constraint and get_or_create prevent duplicate delivery of the same event.
- Notifications are created inside existing task/comment atomic transactions. Tests force notification failure and verify task creation, comments and status changes roll back with their audit writes.

### Permissions and data handling

Every feed, recent menu, unread count and read action is scoped to the recipient and the existing current Task access policy (creator/current assignee/explicit administrator). Former assignees lose notification visibility and read-action access if reassignment removes their Task access. Creators retain their existing access.

Messages contain concise generic event descriptions, not copied comment text, linked-record names or sensitive snapshots. The current Task title is rendered only from authorized rows. Existing linked-record redaction and task history remain intact. No new role hierarchy, private-task flag, permanent-delete action or Customer feature was introduced.

### Due dates

Due state is calculated using timezone.localdate and the existing date conventions:

- Overdue: before today.
- Due soon: today through today plus three calendar days, inclusive.
- Only nonarchived To Do/In Progress tasks qualify; Completed, Cancelled, archived and undated tasks are excluded.
- Daily attention is scoped to tasks the person created or currently holds, not all administrator-visible work.
- Updates shows counts and up to five tasks per due category, with links to matching workspace filters. Cards/details show due-soon/overdue indicators; workspace supports due=soon.
- No due-state notification rows are written on page loads. Marking notifications read does not dismiss live due attention.

No scheduled jobs, new framework, delivery service, email or SMS were added.

### Bell, feed and existing Home Updates

- Shared staff shell has an accessible native disclosure bell, unread badge, five recent notices, due-attention link and full-feed navigation. Counts above 99 display 99+ while the accessible label retains the total.
- Native disclosure works without JavaScript. Escape closes and restores summary focus; outside clicks close; the enhancement defers Escape while the existing task dialog is open.
- Dedicated Updates page at /operations/tasks/updates/ lists latest events first, with stable timestamp/UUID ordering and 20-item pagination.
- Explicit CSRF-protected POST controls mark one or all currently accessible notifications read; writes are idempotent. Redirect destinations are restricted to named app routes and bounded numeric pages.
- Existing Home Updates placeholder now displays the same recent scoped notices, mark-all and View all. Quick Actions and Analytics remain in their established layout; the four-quadrant redesign is deferred to E.
- CSS uses existing BTW shell/components, focus outlines and responsive feed/due columns; no dependency or deployment configuration changes.

### Model and migration

TaskNotification adds recipient, Task, source AuditLog, kind, concise message, created_at and nullable read_at. Index covers recipient/read/creation ordering. Unique constraint covers source_event and recipient. Foreign-key cascades apply only to dependent notifications and do not change existing Task/comment deletion policies.

Additive migration: apps/operations/migrations/0004_task_notifications_9g2d.py. No data backfill or existing-field alteration. Forward, reverse to 0003 and forward to 0004 were exercised on a separate disposable SQLite database. A future separately authorized D deployment must apply this migration through the established deployment process.

### Automated verification

Final accepted runs:

- Focused D: 32 tests, all passed (31 backend/UI permission/due cases plus the frontend adapter test).
- Full Django suite: 802 tests, 801 passed and one PostgreSQL-only skip; no failures/errors. Explicit module labels were used because apps is a namespace directory.
- Earlier combined task/internal interface run: 231 passed, before the last two additional focused rollback/history cases; final full suite includes both additions.
- Actual notification JavaScript adapter: 14 assertions; syntax check passed. Existing shell and quick-create keyboard adapters passed in the full suite.
- Django system checks: no issues. makemigrations --check --dry-run: no changes detected.
- Disposable migration forward/reverse/forward: passed. Diff/whitespace checks: passed.
- Added/changed source was reviewed for scope, accidental data and credentials. Four full-file pattern hits were pre-existing synthetic test-password fixtures, verified unchanged against C; no new secret was introduced.

Regression expectations for the old disabled Home Updates placeholder were updated to the live feed. The archive regression now checks Task list results directly because an authorized archive notification may legitimately appear in the shared bell. Existing permissions, task lifecycle, C filters/modal/context creation, legacy compatibility and audit-history regressions pass.

### Actual D browser verification and limitations

D ran on a private Codespaces forwarded port against an isolated synthetic SQLite database, separate from Render:

Verified in regular Chrome at desktop sizing:

- Bell showed 28 unread events and five recent items with clear type, Task title, message and timestamp.
- Due attention showed seven overdue and six due-soon synthetic tasks, with bounded lists and workspace links.
- Updates cards, current Task links and pagination to Page 2 of 2 were visible.
- A single Mark as read POST changed its item to Read and reduced the unread count from 28 to 27.
- Bell Escape dismissal returned focus to SUMMARY. Desktop client/scroll widths both 1425 (no horizontal overflow).
- Screenshots captured desktop feed, bell and due attention.

After the owner reconnected Chrome, a fresh controlled tab recovered and the remaining checks completed:

- Tablet 820 x 1180: two-column due attention and feed remained usable; client/scroll widths both 805.
- Mobile 390 x 844: readable bell disclosure and feed cards, single-column due attention; client/scroll widths both 375. Bell control measured 44px wide.
- Mark all as read changed remaining 27 notices to Read, removed read buttons/unread styling, set badge count to zero and displayed confirmation. The seven overdue/six due-soon counts remained intact.
- Mobile Escape returned bell focus to SUMMARY; a click outside the disclosure closed it. The navigation drawer opened with focus inside and closed with Escape.
- Due attention linked to the correct Task details and displayed Due soon. Existing mobile quick-create opened with initial Title focus, wrapped backward/forward between Close and Cancel, and Escape returned focus to the floating Add Task button.
- Existing Home Updates displayed the recent read notices and real View all route while retaining Quick Actions/Analytics.
- A separate empty synthetic staff account showed zero unread/overdue/due-soon and the helpful empty feed, with mark-all disabled.
- Temporary viewport overrides were reset successfully after the recovered review and the final temporary review tab was closed.

The earlier Chrome connection stall/debugger detachment is retained as a tooling incident; it was resolved for final verification without changing the app or redeploying Render. Some explicit screenshot captures timed out; the supported screenshot fallback provided the saved mobile evidence. No formal screen-reader accessibility audit was performed.

PostgreSQL was not tested: no approved disposable PostgreSQL endpoint was available and psql/postgres/initdb were absent. The existing ticket-allocation concurrency test (apps.events.test_ticket_workflow_7c2.TicketAllocationConcurrencyTests.test_parallel_allocations_are_unique) remains skipped under SQLite. No Render database was used for tests.

### Preservation and delivery boundary

All 20 pre-existing worktrees were compared with the task-start snapshot: HEAD, branch, porcelain status, tracked/staged diffs and untracked-file fingerprints matched. This includes A/B/C, migration handoff, Build 6B and the old dirty preview checkout. Only the two authorized C remote-ref changes occurred; D's remote branch is absent.

No production changes, dependency/configuration changes, scheduled jobs, destructive migrations, D push/deployment, E or Build 10 work occurred. D remains local for owner review. Available desktop, tablet and mobile browser checks completed after Chrome reconnected; PostgreSQL and a formal screen-reader audit remain verification limitations.

### Files in the focused D change

- DOCS/BUILD_9G2D_UPDATES_NOTIFICATIONS.md
- apps/internal_ui/context_processors.py
- apps/internal_ui/test_home_dashboard_phase1.py
- apps/internal_ui/test_home_dashboard_phase1b.py
- apps/internal_ui/test_workspace_finalization.py
- apps/operations/apps.py
- apps/operations/due_dates.py
- apps/operations/migrations/0004_task_notifications_9g2d.py
- apps/operations/models.py
- apps/operations/notification_views.py
- apps/operations/notifications.py
- apps/operations/task_access.py
- apps/operations/test_notifications_9g2d.py
- apps/operations/test_notifications_frontend_9g2d.py
- apps/operations/test_task_foundation_9g2a.py
- apps/operations/urls.py
- apps/operations/workspace.py
- scripts/test_notifications_9g2d.cjs
- static/internal/css/notifications.css
- static/internal/js/notifications.js
- templates/internal/base.html
- templates/internal/dashboard.html
- templates/operations/notification_bell.html
- templates/operations/task.html
- templates/operations/task_rows.html
- templates/operations/tasks.html
- templates/operations/updates.html
