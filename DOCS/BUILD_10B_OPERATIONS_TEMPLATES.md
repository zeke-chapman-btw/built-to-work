# Build 10B — Operations Templates and Event Setup

Implementation/local verification only. Baseline 9bf5423474cc88312fa35bfb125a1ea7e049ae16; isolated /workspaces/btw-build-10b on build-10b-operations-templates. No push, merge or Render/production migration is authorized.

## Architecture inspection and decisions

The existing operations app owns the new reusable definitions, revision resources and immutable application provenance. Existing Event, EventAssessmentCategory, QuestionSet, EventStation, GameDefinition/EventGameConfiguration remain authoritative. No parallel assessment, station, game, staff, task or participant system is introduced. Explicit events.services.audit_action records actors and minimal operational metadata. Original 10A shared header, tabs and contextual task workflow remain.

- Published question sets can be selected, one version per category and at least two categories when quizzes are included. Existing superuser-only event quiz editing authority is retained even if the caller has template-application permission. Curated quiz pools remain in the existing editor.
- Existing active station records produce event assignments through assign_station. Template application never activates physical station context, replaces a station assignment, or disables another event.
- Existing active Duck Hunt definitions produce EventGameConfiguration with empty settings and current existing default rules. Custom settings remain in existing Django admin. Unknown adapters and scoring rules are not invented.
- Registration integration copies only group mode, group label and optional positive capacity. It never opens public registration, sets consent/legal documents, changes a slug, copies registration responses, or changes event dates. Any different nondefault settings or registration history block replacement.
- Simulator capture profiles contain device-specific calibration data and lack a reusable version/publication/editor contract in this baseline. Existing configurations/results remain visible through authorized links; reusable calibration is deferred rather than pretending a template configures it. Protected 6B XML work remains separate.
- Forms/consent, staffing, tasks, notifications, offline preparation and results are not templated. No task generation, sync expansion or Health Monitor is implemented.

## Revision and application safeguards

Templates have independent draft/published revisions and retirement without deletion. Resources use protected foreign keys to actual catalog records. Publication captures exact resource identifiers, versions/settings and labels in a hashed snapshot. New revisions or independent duplicates copy configuration only. Published definitions and event application records are guarded by server-side validation plus SQLite/PostgreSQL triggers against direct SQL/QuerySet update/delete and resource insertion after publication.

Application is additive: signed expiring actor/event/revision-bound preview, explicit CSRF-protected confirmation, current configuration fingerprint, transaction/event row lock, uniqueness constraints and exact-token idempotency. Conflicts block the complete application, preserving settings. Already-configured equal resources are retained. Reapplication or automatic upgrades are deferred. Future/current/terminal lifecycle checks conservatively prevent template application after an event starts or experience history exists. Existing domain editors retain their established policies for later event-specific customization. Events retain the applied revision snapshot independently of later draft changes or template retirement.

## Permissions

Active staff status is required but never sufficient. Existing default operations.view_operationstemplate permits template viewing. New narrowly scoped permissions: operations.manage_operations_templates (with view permission) for draft/revision/publication/retirement; operations.view_event_operations_setup plus events.view_event for setup overview; operations.apply_operations_templates plus setup/template view and events.change_event for application. Resource authority is additionally required: quizzes preserve existing superuser-only edits, games require games.add_eventgameconfiguration, stations/registration retain events.change_event. No automatic group or staff grants are seeded.

## Carry-forward correction

10A Station assignments linked to a POST-only action and returned 405. 10B points authorized users to the existing GET Results station panel anchor. The mutation endpoint, validation and permissions remain unchanged. Users without the complete existing Results permission predicate are not shown a misleading station-editor link.

## Verification and release boundary

See the final implementation/verification report for fresh test results, migrations, browser evidence, changed-file manifest and preservation audit. Migrations must be reviewed and separately authorized before any Render release. Database guards are deliberate protections for historical definitions; destructive maintenance requires a separate approved procedure. No PostgreSQL concurrency certification may be inferred from SQLite tests.

## Final local verification (2026-10-21)

- Focused Build 10B: 55 tests passed. Established full Django discovery: 917 tests run, 916 passed and one existing PostgreSQL-specific skip under SQLite. System checks and migration consistency passed. Existing frontend task-modal/notification suites: 42 assertions passed.
- Isolated SQLite migration compatibility: forward from operations 0004, reverse to 0004 and forward again all passed; representative existing event, station assignment and game configuration remained unchanged; no templates were seeded. PostgreSQL trigger execution and concurrent transactions remain unverified in this environment.
- Regular Chrome against private Codespaces port 8024, using a separate synthetic SQLite database: list/create/edit/publish/detail/duplicate, preview/confirm/application, shared Event Info/Staff/Tasks/Results and corrected station navigation were reviewed. Event capacity was customized from 40 to 45 through the existing Event editor; published revision and application snapshot retained capacity 40. Desktop (1484px), tablet (820px) and mobile (390px) showed no document horizontal overflow. Browser role coverage was the synthetic superuser; unauthorized direct routes and granular permission combinations were verified by automated tests rather than separate browser sessions.
- Existing Event editor limitation observed: minute-only date/time fields can exclude second-level confirmed staffing times when saving an otherwise unchanged schedule. The synthetic fixture window was expanded by one minute at either end to complete the customization exercise. No Event editor, date validation or staffing implementation was changed for this issue. Existing /accounts/login/ redirect issue remains outside this build.
- All changes remain unstaged and uncommitted in the isolated Build 10B worktree. No release is authorized. The final report and screenshots are in the owner workspace outputs/build-10/10b folder, outside Git.
