# Build 9G2-C — Tasks workspace and quick creation

Implemented October 20, 2026 in the existing Django application. This build is locally committed for owner review; GitHub preservation, integration and deployment require separate owner authorization.

## Baseline and boundaries

- Repository: zeke-chapman-btw/built-to-work.
- Required and verified parent: e2a4fa2a2f7eb7beaca14a7a700028c4c0db69d0 (9G2-B).
- Branch: build-9g2c-tasks-workspace.
- Isolated worktree: /workspaces/btw-build-9g2c.
- At the start, GitHub's B and render-preview branches and Render's last successful revision were verified at B. This build does not update any of them.
- No new models, migrations, dependencies or deployment configuration. No operational database or Render writes. No D, E or Build 10 work.
- All 19 pre-existing worktrees are compared against their starting branch, HEAD, porcelain status and content fingerprints. Remote branch heads are compared with the starting snapshot before/after the local commit.

## Implemented behavior

Tasks now has a direct staff navigation entry and a card-based daily workspace. My Tasks is the default and contains current assignments. Assigned by Me uses immutable created_by, including tasks reassigned to others. All Tasks means the existing authorized scope, not every task for ordinary staff. Archived is separate.

Summary counts cover all active tasks the user can access; tab counts describe each unfiltered view. Search covers title/description. Status (including Cancelled), priority and due filters are available; explicit task administrators also have assignee and accessible Event filters. Event filtering includes primary and additional links. Results are paginated by 20, with open work first, due dates before undated work, then priority, creation time and a stable primary-key tiebreaker. Legacy open/completed/overdue deep links remain accepted.

Cards show status, priority, overdue state, due date, assignee, creator and linked-record indicators. Names remain scoped and redacted through the existing Event and Participant policies. Task permission does not grant record access.

The top and floating + Add Task links progressively open a native dialog. The same server form also works as a full page with ordinary POST/redirect. It reuses TaskForm, save_task and collaboration.add_links. Eligible active staff and self-assignment remain supported. Optional record selections start with 20 eligible records; name-only search returns at most 20 results, preserves selected records and validates submitted choices again on the server. Quick creation permits up to 20 links of each type; more can be added through existing collaboration afterward.

Creation and optional links/audit entries are atomic. A signed, user-bound UUID token expires after two hours and becomes the new Task primary key. Retrying the same valid submission returns the original task without overwriting it or creating duplicate audit entries. Existing database uniqueness handles competing inserts; PostgreSQL concurrency is not claimed verified here. No token values or credentials are stored in this document.

The dialog uses native modal behavior plus explicit Tab boundary handling. Escape and close restore focus; submission blocks repeated saves and closing. Validation retains entered values and focuses the error summary. Network errors retain the form/token for a safe retry. Success provides View task and Back to my work; closing from the workspace refreshes counts/results while preserving filters. Native links/forms remain usable without the enhancement.

Event detail, confirmed/otherwise authorized staff Event pages, and canonical staff Participant profiles have context actions. Context routes recheck the actual Event/Participant policy. Archived Events and archived/system-test Participants cannot become new context targets. Event context fixes the compatible primary Event FK; Participant context is mandatory even if a posted selection omits it. Additional eligible links are supported. Unauthorized/forged contexts fail without revealing record names.

Task detail, edit/status/archive/restore, linked-record forms, comments and activity navigation retain a strictly local, allowlisted workspace return path. External/malformed return URLs are rejected. Existing creator/current-assignee/operations.manage_tasks policy, legacy missing creators, audit/history, comments, linked records, archive/restore and Cancelled behavior are retained.

UI modernization uses the existing BTW shell, branding and controls: summary cards, responsibility tabs, readable task cards, distinct actions, consistent badges, comfortable touch targets, visible focus and a scrolling mobile dialog. No frontend framework was introduced. This is Base44-inspired refinement; no fresh pixel-perfect comparison or exact Base44 match is claimed.

## Source map

- apps/operations/workspace.py — scoped queries, counts, filters, stable ordering and safe return paths.
- apps/operations/task_workspace_views.py — workspace, quick-create and name-only lookup endpoints.
- apps/operations/quick_create.py — contextual form, signed retries and atomic creation orchestration.
- apps/operations/templatetags/task_ui.py — authorized context actions.
- apps/operations/services.py, collaboration.py, collaboration_forms.py — minimal reuse hooks for a signed creation ID and permission-scoped selected lookup results.
- apps/operations/task_access.py — privacy-aware card indicators.
- apps/operations/views.py and urls.py — workspace integration and return navigation.
- apps/internal_ui/navigation.py — Tasks destination and unique active highlighting.
- templates/operations/tasks.html, task_rows.html, quick_*.html, task_dialog.html — workspace and progressive forms.
- templates/operations/task.html, task_form.html, task_links.html — context-preserving actions.
- templates/internal/base.html, templates/events/event_detail.html, templates/staffing/staff_event.html, templates/participants/staff_profile.html — shared assets and contextual entry points.
- static/internal/css/tasks.css, task_create.css and static/internal/js/task_create.js — responsive presentation and enhancement.
- apps/operations/test_task_workspace_9g2c.py, test_task_modal_frontend_9g2c.py and scripts/test_task_modal_9g2c.cjs — new regression coverage.
- Five existing internal_ui test files update exact navigation/button expectations for the added Tasks workspace/shared dialog; existing permission and homepage assertions remain.

## Verification

Final focused Tasks suite: 142 passed. Full Django suite: 770 run, 769 passed, one PostgreSQL-only concurrency test skipped. Django system checks and migration consistency checks pass. JavaScript syntax, actual modal-script adapter (25 assertions), seven shared-shell focus tests, CSS parsing and diff whitespace checks pass. Changed files are scanned for credential patterns and reviewed for scope before staging.

Tests use explicit config.settings.test, an empty DATABASE_URL and SQLite disposable paths. Because apps is a namespace package, discover explicit module labels rather than relying on manage.py test apps.operations or an unqualified empty discovery run. For example, from this worktree:

~~~sh
export DJANGO_SETTINGS_MODULE=config.settings.test
export DATABASE_URL=''
export DB_ENGINE=sqlite
export DB_NAME=/tmp/btw9g2c-disposable.sqlite3
export PYTHONDONTWRITEBYTECODE=1
/workspaces/built-to-work-build-8a/.venv/bin/python manage.py check
/workspaces/built-to-work-build-8a/.venv/bin/python manage.py makemigrations --check --dry-run
node --check static/internal/js/task_create.js
node scripts/test_task_modal_9g2c.cjs
node scripts/test_shell_focus_9g.cjs
~~~

The existing venv above was reused; no dependencies were installed or modified. Full test labels were derived from apps/**/test*.py by converting repository-relative file paths to dotted module names.

Regular Chrome browser verification succeeded through the existing private Codespaces port using a temporary server/settings file and an isolated synthetic SQLite database outside the repository. Verified desktop workspace/views/filtering, quick creation with two Events/two Participants, required-field feedback, modal focus/Tab wrapping/Escape/restoration, success refresh, filtered detail/edit/cancel context, Event-context primary association, mobile Participant-context required association plus an additional Participant, and native full-page form creation. Desktop (1440), tablet (900) and mobile (390) layouts were inspected; no horizontal overflow was measured. Mobile dialog scrolls with a sticky close action; the floating action meets its 48px minimum. Accessible help references resolve after final template reload.

Browser review found and corrected missing initial SelectMultiple choices; a rendering assertion now covers this. Native browser-required validation was observed; server-invalid forms, CSRF, unauthorized users, forged links, retries and audit rollback were verified with isolated automated requests. Browser tests used one synthetic administrator, so the complete multi-user role matrix is automated rather than separate live browser sessions. The native full-page browser flow was tested; JavaScript was not globally disabled in Chrome. The Node adapter exercises the shipped enhancement but does not replace real browser accessibility testing.

The skipped test is apps.events.test_ticket_workflow_7c2.TicketAllocationConcurrencyTests.test_parallel_allocations_are_unique (SQLite cannot verify row-lock allocation). No approved disposable PostgreSQL instance was available and local PostgreSQL tools were absent. No live Render database was substituted. Concurrent worker behavior remains an explicit verification limitation. No formal assistive-technology certification or exhaustive browser/device matrix is claimed.

Screenshots and the owner-facing implementation/testing report are local outputs under outputs/build-9g/phase2c/ in the owner's BTW Platform folder; they are not application assets and are not included in the repository commit. Synthetic fixture records contain no actual candidate, employee or customer data.

## Review and next gate

The local commit containing this file is the deliverable checkpoint (use git log -1 on this branch for its SHA). Review the complete change and local screenshots. Owner authorization is still required for a GitHub push, merge/integration, render-preview update, deployment or live migrations. Render remains on B. Stop here; do not begin 9G2-D, 9G2-E or Build 10.
