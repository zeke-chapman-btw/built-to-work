# Build 9G2-A — Task foundation and initial task UI

Baseline: Build 9G1, 52eeea04379b216a69e0e80b49505f28287616a6.

## Approved access rule

An active authenticated Django staff login can create a task. The original creator,
current assignee (StaffProfile.user), and an active staff user with the existing
operations.manage_tasks permission may view and fully manage it. Django superusers
receive this permission through Django's existing authorization backend. is_staff alone
never grants access to other people's tasks. No manager hierarchy, private flag,
sharing modes or approval workflow was added.

Reassignment preserves created_by, grants the new assignee access and removes the
former assignee's ordinary access unless they are also creator or task administrator.
Existing Tasks and My Tasks routes remain; this is not the later top-level workspace.
The existing Operations Tasks link now opens the same filtered task list for staff.

New assignments require an active StaffProfile linked to an active is_staff User.
Creators do not need a second identity model or a newly provisioned staff profile.
An existing ineligible assignment may remain unchanged during authorized edits.
Missing creators remain null: a legacy task is accessible to its linked current
assignee and explicit task administrators; an unowned, unlinked legacy task is
administrator-only. No creator is invented and no assignment is rewritten.

## Lifecycle and compatibility

To Do, In Progress, Completed and Cancelled are retained. Creator/assignee/admin may
edit details, reassign, change status, complete, reopen, archive and restore.
Archive metadata is separate from status; ordinary lists and event task lists omit
archived tasks. Authorized users retrieve them through Archived or the detail URL.
No delete endpoint or permanent deletion feature exists.

Task lifecycle authorization is independent of related event state: a draft/archived
event does not remove creator/assignee rights to their task. This intentionally
supersedes the old task-only draft visibility and archived-event write restrictions.
Event, note and document permissions remain unchanged. Linking a new event requires
existing event access and excludes archived events. Existing links can be retained.
Task views, forms and history redact an event's label when the viewer cannot access
that event. Access to an event does not grant access to all its tasks.

The additive 0002 task migration adds nullable archived_at (indexed) and archived_by.
created_by becomes blank=True for validation of its already-supported null legacy
values. There is no data backfill, status conversion, destructive operation or new
business entity. Review migration behavior on disposable PostgreSQL before release
when that environment becomes available.

## History and UI

Existing AuditLog infrastructure records one durable entry per meaningful logical
mutation in the same transaction. Reassignment plus detail changes produces one
reassigned entry containing the complete before/after snapshot. No-op edits add no
entry. Completion, reopening, archive and restore have readable action labels.
Old audit entries remain untouched, including any older duplicate entries.
The detail page exposes a permission-protected, escaped, twenty-entry paginated
history with actor, time and allowlisted changed values. Ordinary users cannot edit
history. AuditLog's existing admin interface is read-only; this is application-level
immutability, not a claim against direct database administration.

Task-only styles extend existing BTW cards/forms/buttons/shell. Lists have active
view states and an Archived view; forms have labels, optional change notes, validation
summaries and keyboard focus indicators; details show assignment, due date, priority,
status and readable history. Mobile grids collapse without reordering content.
No frontend framework, full Tasks workspace, floating modal, Home redesign, comments,
notifications, recurrence or attachments were introduced.

## Verification and release boundary

Use config.settings.test with DATABASE_URL explicitly cleared, DB_ENGINE=sqlite and
DB_NAME pointing to a disposable /tmp database. Explicit focused module labels:

    python manage.py test apps.operations.tests apps.operations.test_task_foundation_9g2a apps.operations.test_task_migration_9g2a --noinput

Full regression discovery uses the existing ProjectTestRunner:

    python manage.py test --noinput --verbosity 2

The migration regression creates legacy cancelled/unowned/unlinked data at the prior
operations schema, applies 0002, and checks task/audit preservation. This runs only
inside the disposable Django test database.

Implementation verification: full suite 716 run, 715 passed, one PostgreSQL-only
allocation-lock test skipped on SQLite. System and migration consistency checks
passed. Browser checks used only a separate synthetic /tmp SQLite database and a
private Codespaces development port; no Render writes or deployment occurred.
The Codespaces proxy rewrites Origin to http://localhost:8024; only the temporary
browser-test settings trusted that exact origin. No committed configuration changed.

PostgreSQL row locking/concurrency remains unverified in this environment; no local
initdb/pg_ctl/psql binaries or approved disposable PostgreSQL instance were available.
Local commit is authorized; pushing, integration and Render deployment require a
separate owner approval. Do not migrate the live Render DB during this build.
