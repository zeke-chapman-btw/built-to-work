# Build 8E-A — Admin Portal Foundation

## Scope

This checkpoint improves the existing BTW operational staff portal at `/internal/`. It keeps the Django administration site at `/admin/` as a separate configuration interface. No Build 8E-B participant directory or later-phase features are included.

## Portal changes

- The operational shell provides grouped navigation for events, participants, operations, assessments, and administration.
- Navigation links are shown only to signed-in staff with the corresponding Django model permissions. Direct requests are also protected by server-side permissions in the affected event workflows.
- The dashboard presents event and active-registration counts only when the user has permission to view those records; workflow cards are permission-scoped.
- The shell includes responsive layout, keyboard focus treatments, and the existing local BTW font/color tokens. Font and other assets are served locally.
- The participant directory is intentionally labeled as planned and is not a live Build 8E-A destination.
- Django Admin remains separately available at `/admin/` to authorized administrators; it is not the operational dashboard.

## Main review routes

- Operational dashboard: `/internal/`
- Event management: use the Events navigation when permitted.
- Participant qualifications, documents, and approvals: use the Participants navigation when permitted.
- Offline preparation, synchronization, ticket delivery, stations, and assessments: use Operations/Assessments when permitted.
- Django Admin: `/admin/`

Routes are resolved by the application URL configuration; the visible navigation is permission-dependent. Users without the relevant permission will not see that link and remain subject to the view's server-side access checks.

## Safety and scope notes

- This pass does not add participant-directory functionality, alter Build 7 or Build 6B, or change public registration or kiosk behavior.
- The dashboard does not expose participant-level data.
- Event and ticket operations use the existing application views and permission model; Django Admin is not treated as a replacement for the staff workflow.
- A staff account with narrowly scoped permissions should be used when reviewing role-specific navigation. Superuser visibility is broader by design.

## Validation

Validation completed: `.venv/bin/python manage.py test -v 2` passed 285 tests with 1 PostgreSQL-only concurrency test skipped (SQLite cannot validate PostgreSQL row locks); the focused portal suite passed 11 tests; `manage.py check` reported no issues; `makemigrations --check --dry-run` reported no changes; and `git diff --check` completed cleanly. The live Codespaces preview at `/internal/` rendered the operational dashboard, and the Approvals navigation loaded `/participants/staff/approvals/` successfully.
