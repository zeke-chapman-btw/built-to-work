# Build 9G2-E — Final Refinement Completion Report

Date: 2026-10-21. Status: implemented and verified; local commit only, pending owner review.

## Checkpoint

- Repository: zeke-chapman-btw/built-to-work.
- Branch: build-9g2e-unified-ui-dashboard.
- Worktree: /workspaces/btw-build-9g2e.
- Starting commit: 32d240e66a4cd501d9010c0524247948121dbc35; clean before editing.
- Ending commit: the refinement commit containing this report. Its exact SHA is recorded in the external completion report and final owner message.
- Preserved D checkpoint and remote render-preview: d19061e5347b0501c2cc89b9e26fd3f5e4f7f011.

## Delivered refinements

1. The existing official SVG appears once in the top header on desktop and mobile. The sidebar duplicate was removed. Dark surfaces, red active navigation, palette, compact shell, and fitting tagline remain.
2. Notifications terminology is consistent across Home, bell, workspace heading/title, pagination, and links. Existing Updates URLs remain. **Live task attention** stays computed and separate from stored notifications and read actions.
3. Home labels its existing permission-aware metrics **Analytics**. **View more** opens central Reports & Analytics, rather than Event Reports.
4. New named route internal:reports at /internal/reports/ organizes existing reports. Event Reports uses its complete existing can_report policy; Workday Reports uses its existing manager policy, including event-view permission. Original report URLs, calculations, filters, and exports remain. No future-feature cards or reporting engines were added. Active staff without report access see an honest empty state. Operations navigation leads to the central landing.
5. One red **+ Create Task** action floats throughout eligible shared staff pages, reusing existing routes, modal, validation, idempotency, assignment/privacy rules, notifications, and keyboard behavior. Static Home and duplicate Tasks primary actions were removed. The optional Home Quick Action and existing contextual links remain.

The shared task_creation_enabled context flag suppresses the floating action per page. It defaults on for active authenticated staff and is off on full-page task creation flows. The action exists only in the internal shell; login/logout, public registration, participant, kiosk, and game interfaces retain their own shells. The template tag independently checks the existing task staff policy, so an override cannot grant unauthorized creation. Event/Participant context passes through existing authorization; inaccessible or archived context falls back to generic permitted creation without exposing the record.

Tasks retains its return/filter query and refreshes its workspace after creation. Other origin pages stay in place, restoring opener focus; counts update on normal refresh. The action has a 48px target, safe-area offset, page-bottom clearance, and visible focus. It becomes inert with main content while the mobile drawer is open.

## Verification

- Focused regression: **401 tests passed**, including 17 new refinement cases and existing task, notification, reporting, workday, and staff UI coverage.
- Full project runner: **833 tests; 832 passed, 1 skipped**. The existing SQLite row-lock allocation test requires PostgreSQL; it was not claimed as passed.
- Shipped modal script: **28 assertions passed**, including surviving/removed opener focus, non-Tasks page return, validation, retry, keyboard behavior, and lookup.
- Django check passed; migration consistency: **No changes detected**; final whitespace check passed.
- Regular Chrome: **1440×900**, **820×1180**, **390×844**. Home, Notifications, and central Reports reviewed at all sizes. Events, Participants, Tasks, existing Event Reports, and mobile form clearance also reviewed.
- Read actions changed unread 6→5→0 while live attention stayed 1 overdue/4 due soon. Self-created tasks added no self-notifications.
- Browser creation succeeded from Reports, Event detail, Participant profile, and mobile Home. Context and workflow were preserved. Required-title validation, Shift+Tab, Escape, focus return, drawer behavior, and modal scrolling passed.
- Event Reports name filtering reduced six synthetic events to one; CSV link retained filters. Actual export response/calculation behavior was verified by automated reporting tests; a browser download was not performed.
- No horizontal page overflow on reviewed layouts. At mobile page end, the final Home metric ended at 601px above the floating action at 784px; event form actions also cleared it.

## Evidence and limitations

Screenshots and the exact-SHA external report are in the local BTW Platform workspace under outputs/build-9g/phase2e/final-refinements/. They contain synthetic records only. Test databases, private-file roots, setup scripts, credentials, and logs stayed outside the checkout.

The pre-existing anonymous staff redirect to missing /accounts/login/ remains an outstanding authentication-routing issue; review used existing admin sign-in. Authentication configuration was not changed. Chrome responsive checks are not a complete cross-browser or WCAG audit. PostgreSQL allocation concurrency remains unverified here.

Earlier temporary verification snapshots were unavailable after the Codespace restart. This task verified the persistent E checkpoint, clean E state, D and remote references, then captured a new pre-edit baseline. All **21 protected worktrees** were compared against that baseline using HEAD, branch, status, tracked/staged binary diffs, and untracked-file fingerprints; all remained unchanged. Historical handoff, 6B work, the dirty old render-preview checkout, and unrelated branches were preserved.

No dependencies, schemas, migrations, production data, or application configuration changed. No push, merge, rebase, or deployment occurred. Build 10 was not started. Stop for owner review.
