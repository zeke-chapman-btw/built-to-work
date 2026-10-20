# Build 9G2-B — Task collaboration and linked records

Status: implemented locally on build-9g2b-task-collaboration, based on approved 9G2-A commit 007e20db7ae491fe807ab296f52069fc1b2d3fc4. This build is not authorized for push, merge, Render deployment, or operational database migrations.

## Scope and access

The creator, current assignee, and active staff with operations.manage_tasks retain full task access. Unrelated staff and former assignees (unless creator/admin) have none. Django is_staff alone does not grant administrator rights. No new task-sharing, private-task, hierarchy, or Customer relationships were added.

Record access remains independent: the existing task_access.events_for policy scopes Events; Participants use the canonical staff directory/profile participants.view_participant permission. Profile sections continue enforcing their own permissions. Labels, selectors, links, and historical record values resolve against current viewer access. Restricted records have neutral indicators and no deep links. Audit link changes store IDs rather than copied record labels or contact details. Free-text comments are visible to task collaborators; the composer explains that boundary.

## Additive data model and legacy compatibility

Migration operations.0003_taskcomment_taskeventlink_taskparticipantlink adds TaskEventLink, TaskParticipantLink, and TaskComment. Unique constraints prevent duplicate links within each type; linked Event/Participant deletion is protected. TaskComment protects its task from deletion. There is no data backfill or change to existing Participant records, ownership, statuses, or Task.event.

Effective Events are the union of the preserved legacy Task.event FK and additional TaskEventLink rows. Legacy Event creation and editing continue using Task.event; Event task views include both kinds once, scoped to task access. Additional links skip the primary Event. Promoting an additional Event to the primary FK removes its redundant link row. Explicitly changing the primary Event retains existing edit semantics (the old primary is replaced); it does not silently convert the old primary into another additional link. Explicit removal of the primary link clears the FK as an audited user action. No migration rewrites it.

Selectors support name searches, bounded to 20 results of each type per search. Additional searches allow more links; there is no total per-task limit. New targets exclude archived Events and archived/non-person Participants. Links may be managed on archived tasks; the restoration requirement applies specifically to comments. Restricted records can be unlinked by authorized task users without exposing the protected label.

## Comments and unified activity

Comments are plain text, limited to 4,000 characters, with native Django CSRF protection and escaping. Authors may edit their own comments while retaining original authorship and creation time. Task administrators may moderate others' comments, with recorded attribution. Other collaborators cannot edit another author's text. No delete endpoint or permanent comment deletion is provided.

Archived tasks and comments remain readable to authorized users; comment creation/editing requires restoration. Reassignment and completion retain comments, with current task permissions applied on every request. Transactional services lock the task before comment updates, recheck task access, and validate revisions. Audit-write failure rolls back the comment change.

The existing task-target AuditLog remains the single history source. Link changes, original comments, edits, moderation, and existing task lifecycle actions appear oldest first, paginated 20 entries per page. Original comment entries keep their original text; revisions appear as later activity containing prior/new text and actor. Idempotent link additions and unchanged comment edits do not create duplicate activity. Historical task entries without link/comment fields remain readable. Linked record labels in history are resolved using current permissions.

## Interface

The existing Django templates and BTW shell remain in use. Task detail has linked-record cards, an accessible native checkbox selector/search page, a comment composer, and combined activity. Task-scoped CSS provides visible focus outlines, wrapping labels, touch-sized controls, and a single-column selector/form layout below 700px. No frontend framework or Home/notification/full Tasks workspace redesign was introduced. No exact new Base44 comparison is claimed.

## Verification and limits

- Django system check passed; migration consistency check reported no changes.
- Final full regression suite: 740 tests, 739 passed and one PostgreSQL-only ticket-allocation concurrency test skipped (53.037 seconds).
- Final focused task, collaboration, and migration suite: 112 passed (9.881 seconds).
- Additional HTTP workflow test covers successful linking, comment posting/editing, unlinking, and archive enforcement. Archived mutations return the existing 409 conflict response.
- Forward and reverse migration testing on disposable SQLite preserved the legacy Event FK, Cancelled/unowned/unlinked task, and historical audit. Reversal was tested before any new link/comment data; reversing 0003 drops the new tables and is not lossless after new collaboration data exists. Do not treat reversal as an approved rollback.
- Existing frontend shell syntax and seven keyboard/focus regression checks passed. Responsive styles were reviewed in source. Browser access to the private Codespaces test URL was blocked with ERR_BLOCKED_BY_CLIENT; actual desktop/mobile visual verification remains outstanding. Disposable server /health/ returned HTTP 200. No live preview or operational data was used for implementation verification.
- No approved disposable PostgreSQL instance was available; psql, initdb, and pg_ctl were absent. PostgreSQL row-lock/concurrency behavior remains unverified. Render was not used as a substitute.

## Release boundary

9G2-A was preserved on its matching remote branch at the exact approved commit. 9G2-B is limited to one local implementation commit. Protected worktrees and unrelated remote heads must match the captured baseline. Render-preview remains at 52eeea04379b216a69e0e80b49505f28287616a6. Any 9G2-B push, merge, deployment, operational migration, or continuation into 9G2-C/Build 10 requires separate authorization.
