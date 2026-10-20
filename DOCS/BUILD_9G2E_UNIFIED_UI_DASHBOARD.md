# Build 9G2-E — Unified Interface & Home Dashboard Implementation Report

## Baseline and scope

- Repository: `zeke-chapman-btw/built-to-work`.
- Branch: `build-9g2e-unified-ui-dashboard`.
- Worktree: `/workspaces/btw-build-9g2e`.
- Verified clean starting commit: `d19061e5347b0501c2cc89b9e26fd3f5e4f7f011`.
- The prior D release was verified through its completed release gate and exact GitHub development/Preview references before E implementation.

## Delivered

The existing Django template/CSS application now has a compact shared shell: a 190px desktop sidebar, dark branded header, red active navigation/actions, consistent cards, headings, forms, validation/focus treatment, tabs, pagination, native filter disclosures and the existing task modal. The approved colors are #191919, #D91F2A, #FFB81C, #F4F5F6 and white. Equipment yellow is restrained to selected icons/focus accents; semantic status colors retain text labels.

Home uses the approved desktop two-column arrangement and mobile reading order: My Tasks, Recent Updates, Quick Actions, At a Glance. It previews four assigned open tasks using existing workspace ordering, access and due presentation; three recent D notifications using the existing recipient/read policy; permission-filtered saved shortcuts; and real, explicitly scoped counts. Global Event/Participant counts require their destination permissions. No reporting engine or notification system was duplicated. Record previews are bounded; query-count tests cover growth from one to 81 tasks.

Default shortcuts prioritize Create Task, Create Event and Add Participant while respecting permissions. Stored orders and deliberately empty selections are preserved. The nine-action limit remains; Quizzes stays selectable in the editor. Add Participant reuses its existing permission-protected Django admin form.

Events retains all status, search, type and UTC overlap/date filters. Native advanced filters expand for active criteria/errors. Compact cards retain location, local dates/timezone, type, status and finalization. Confirmed staffing counts/requirements appear only for staffing managers. Tasks keeps its existing views, filters, modal, links, comments/history and archive/restore; its filters/cards and Updates are compacted. Participants uses the same shell, compact search/table and existing profile permissions. Reports and Workday reports have shared compact filter grids; Staff, operational pages and Settings inherit the shared presentation.

## Verification

- Django system checks: passed; no issues.
- Migration consistency: passed; no schema changes or new migrations.
- Focused regression: 283 passed.
- Full project runner: 816 tests, 815 passed and one skipped. The existing ticket allocation row-lock test requires PostgreSQL and cannot run on SQLite.
- Diff/whitespace checks: passed.
- Regular Chrome, isolated private Codespaces Preview with synthetic data: desktop 1440×900, tablet 820×1180, mobile 390×844. Home, Events, Tasks, Participants/profile, Updates and representative Reports/Staff/Settings were inspected.
- Verified individual notification read (6→5), read-all (5→0), persistent live due attention, Home quick-create success/no self-notification, Event type filtering, task due/priority filters, Participant search/profile navigation, mobile drawer/Escape focus restoration and modal initial/return focus.
- No page overflow on the core checked mobile pages; status tabs and Participant tables scroll within their containers. Mobile form controls retain 44px heights.
- Calculated contrast: white/red 5.02:1; dark/equipment-yellow 10.15:1; muted/white 6.31:1; blue focus/white 6.35:1; shared control boundary/white 3.67:1.

## Limits and continuity

This is a shared staff application presentation build. Complex legacy detail pages retain their specialized structures. The reused Add Participant admin form retains Django's admin layout; public intake, participant account and kiosk/game experiences were not wholesale rethemed. Cross-browser/device and comprehensive assistive-technology audits were not performed. The pre-existing anonymous /accounts/login/ redirect has no corresponding route in the baseline; local review used the existing /admin/login/ endpoint. Authentication routing was not changed in E.

The 21 protected worktrees and remote branches are preserved. E is intended as one reviewed local commit only. No push, merge, deployment, dependency or application configuration change, production access or Build 10 work is part of this build. Screenshots and the exact resulting commit SHA are supplied in the owner-facing completion report outside the repository.
