# Build 9B — Event Management Foundation & UI

Baseline: 81d2a2f74ebdbb106b2b099c248ba694b5f1a395. Isolated branch build-9b-event-management, worktree /workspaces/btw-build-9b. Reuses the existing Event UUID, User permissions, audit log, registration, QR, assessment, Duck, simulator and offline services.

## Management contract

- New drafts retain required name, start/end window and timezone; logistics remain optional. Save Draft and Publish to Staff are explicit form actions.
- Draft → Upcoming (publish); Upcoming → Completed; Draft/Upcoming → Cancelled; Draft/Completed/Cancelled → Archived. Archive an upcoming event only after completion/cancellation. Archived management details are read-only. Related historical registrations and results are not deleted or rewritten.
- Current is derived from Upcoming plus the existing start/end window. It is never a stored status and does not silently complete overdue events. All events remains available for historical or overdue records.
- Publishing requires existing events.change_event (and events.add_event when creating). It records actor/time but does not open public registration, send invitations, create assignments or grant staff access. Staff-facing scheduling remains Build 9C.
- Details finalization has its own actor/time. An actual edit reopens finalization; an unchanged save preserves it. Lifecycle changes do not falsely finalize details.
- Writes lock the Event and require the revision shown when editing/confirming. Stale edits/actions are rejected rather than overwriting newer changes. Lifecycle actions use POST, CSRF and explicit confirmation; cancellation/archive require an audit reason.
- The old edit snapshot is captured before ModelForm validation mutates the bound instance. Snapshots normalize aware datetimes to UTC. Django Admin status is read-only; Admin edits are audited and cannot bypass lifecycle actions or edit archived events.

## Fields and scheduling

Migration 0013_event_management_9b is additive. It adds event type/custom description; setup, staff-arrival, optional daily hours and arrival-home fields; event contact, housing and logistical instructions; publication and details-finalization actor/timestamp metadata. Existing core dates, fields, identities and relationships are retained.

The existing start_at/end_at remain the authoritative QR/kiosk window. Setup/travel/daily planning fields do not extend that window. The form interprets local date/time entries using the event timezone; invalid zones, reversed dates, daylight-saving ambiguous/nonexistent times and conflicting logistics are rejected. Daily hours require both values and same-day opening before closing within the first/last event bounds. Overnight experiences can use the core event window; an overnight recurring daily-hours schedule is not modeled in this build. Existing ticket expiry is retained when event details are edited.

List date filters use UTC calendar dates and match overlapping event windows; dates displayed on records use the event timezone. Search covers event name, code, venue, city and custom event type. Status/type/search/date filters are validated and retained through pagination (20 records per page).

## UI

Events has Upcoming, Current, Completed, Draft, Cancelled, Archived and All events views. Detail has a stable header, Overview, Results & readiness and authorized Registrations sections. Mobile uses a labeled section selector and submit button. Build 8E-D scores, readiness, offline metadata, exceptions and station tools remain in Results. Registration, public-registration configuration, assessment setup, ticket recovery and offline preparation remain related tools under their existing permissions.

Only Events CSS/templates are restyled. The shared sidebar, Home/Quick Actions, Settings gear, Participants, public registration, kiosk and simulator design remain unchanged. Use existing BTW tokens, fonts and cards. Staffing, basic tasks and reports are identified as unavailable without fake counts or buttons. No task/checklist/template system is included; future tasks follow the user's simpler create/assign/due-date/status workflow.

## Offline boundary

Reviewed the existing downsync EVENT_FIELDS/EVENT_DATES allowlists and import conflict rules. New logistics/contact/publication metadata is deliberately excluded from the existing registration configuration wire contract. No silent protocol extension, staff-data download or result-sync engine is introduced. Existing core event fields and their synchronization remain intact. This cloud-management metadata needs an explicitly approved/versioned offline extension if it is required on a disconnected trailer later.

## Verification

Thirty focused Build 9B tests cover creation, publication, lifecycle, permissions, CSRF, stale writes, audit snapshots, scheduling/DST, filters/pagination, offline exclusions and actual accepted experience/QR preservation. The full normal regression suite passes 409 tests with one skip. Populated migration verification retains the original Event, Participant, EventRegistration, QR token/expiry and ExperienceSession identities through 0012 → 0013. No migration drift or Django/diff-check issues.

Browser checks use only clearly SYNTHETIC local data. Desktop review includes retained edit values and results; mobile review at 390px includes draft creation, publishing, filtering, finalization, result-section selection and no page overflow. Render publication must be a normal push preserving any newer preview commits, followed by Live/health/runtime and existing feature verification. No main/production/Render infrastructure changes.

Known older partial-label test setup errors were previously reproduced on the unchanged baseline; the normal full suite is the acceptance run. Do not claim the old partial selection was rerun in this build. Physical simulator connectivity and fully disconnected operation are not certified by these changes.
