# Build 9C — Staff Management, Scheduling & Assignments

Baseline: 63838615fdde39e479a16752c2daa7317f4b6d94 (verified Build 9B preview). Branch build-9c-staffing; isolated Codespace worktree /workspaces/btw-build-9c. No main, production, Render infrastructure or unrelated worktree edits.

## Identity and permissions

The staffing app adds an operational StaffProfile, not a second login or participant identity. Profiles may be roster-only. Creation never creates or links a User. Existing active staff accounts can be linked/unlinked only with staffing.change_staffprofile, staffing.link_staff_account and an explicit confirmation. Linking does not change account activity, passwords, groups or permissions. No email auto-matching or staff imports are performed.

Staff directory/profile reading requires staffing.view_staffprofile; profile creation/editing require the corresponding add/change permissions. Event staffing requires staffing.manage_event_staffing plus events.view_event. Permissions are granted through the existing Django User/group administration; no manager grants are automatically assigned by migration. General staff need an existing active Django staff account explicitly linked to an active profile. They receive access only to their own profile, requests, schedule and published event summaries; scheduling does not grant Event management, participant, payroll or other employees' private-data access.

Private profile notes remain manager-only. Before confirmation, event summaries expose dates, location, type and sign-up eligibility, not operational housing/contact instructions or participant records. Confirmed employees receive the event's operational instructions and housing information. Former assignees retain a safe event summary and assignment history, without current operational details.

## Publication, deadlines and calendar

StaffingPlan is one-to-one with the existing Event. A manager explicitly publishes the staffing opportunity; it is available only while the Event is Upcoming and not ended. Drafts are never listed. Legacy Upcoming events without the Build 9B publication timestamp remain compatible; explicit plan publication controls availability without backfilling or modifying Event metadata or public registration.

The sign-up deadline is stored as an aware datetime, entered/displayed in the Event timezone and validated for ambiguous/nonexistent DST times. New requests close at the exact deadline. Editing, extending or clearing the deadline changes eligibility immediately without deleting requests/assignments. Existing requests can still be approved and managers can directly assign/edit staff after the deadline.

Daily coverage includes setup through the last event operating day; arrival-home travel does not extend the staffing calendar or QR/kiosk window. Event-wide default headcount can be overridden for each day, including zero. Summaries explicitly use staff-days for multi-day totals; daily tables show required, confirmed, pending and remaining coverage. One person counts once per day; pending requests never count as confirmed. Roles are descriptive responsibilities, not pay rates or multiple coverage slots.

Daily staffing is bounded to 366 days. Working and arrival times are optional per day; blank working times use the existing Event daily hours or operating window. Setup days default to a whole-day commitment, so managers should set times when partial setup coverage is intended. Explicit daily work must start/end on the same local day; a recurring overnight shift engine is outside Build 9C. Arrival cannot follow work start. Confirmed conflicts include arrival time and are compared in absolute time across events/timezones.

## Requests and immutable assignment revisions

AvailabilityRequest records the employee's original offered dates and note. Requests start Pending. Approval atomically creates an immediately Confirmed StaffAssignment for those dates, records the decision and updates coverage/schedule. No second employee acceptance is required. Decline creates no assignment. Managers cannot decide their own requests. Duplicate pending/approved requests and duplicate confirmed event/staff assignments are prevented with service validation and database constraints.

Direct assignment is separate from requests and never creates a fake availability record. Managers can edit dates, daily times, arrival and role, reassign staff, or remove assignments. An edit marks the old assignment Replaced and creates a new confirmed revision with supersedes/source-request links. Daily rows on older revisions are retained. Removal marks the assignment Removed, preserving days, request/approval history and audit records. An approved request can therefore coexist with no current assignment; the employee UI explains this explicitly.

Event row locks serialize plan/request/decision changes. StaffProfile locks serialize competing assignments across events; multi-person locks are ordered. Writes use atomic transactions and revision checks. Conflicting/stale decisions roll back without partial assignments or coverage changes. All form writes retain CSRF protection. Existing audit services record profile changes, account links, daily requirements, requests, decisions and assignment history.

Event date/timezone edits cannot silently strand confirmed staffing. Portal validation rejects incompatible calendar changes; Admin protects those calendar fields while confirmed assignments exist and validates again under the Event lock. Deactivating a profile preserves history and existing assignments, labels the profile inactive, blocks new assignments and self-service, and does not silently remove scheduled commitments.

## UI and integration

Operations links expose the authorized Staff directory and paginated pending-request queue. Events exposes My staff schedule and a manager Staffing section. The approved six-item sidebar and Settings gear remain; existing permission-based visibility is retained. Manager forms provide real create/edit/link/deactivate/needs/deadline/approve/decline/direct-assign/edit/reassign/remove workflows. Event assignment and profile history are paginated. Staff-facing Available events, My requests, Confirmed schedule/history and My profile use scoped queries and responsive cards.

Staffing reuses BTW fonts, tokens, card/form styles and small scoped staffing CSS. Global Home, Participants, kiosk, simulator and public registration are unchanged. Event UUIDs, registrations, tickets, accepted experiences, 8E-C approvals and 8E-D results remain in their existing models/services. Staffing/private data is not added to the offline registration export/import contract.

## Reference and verification

Reviewed Build 9A discovery and Build 9B source/report. The requested warm-crew-flow-pro.base44.app URL returned App not found. The authenticated Base44 editor remained accessible: staff directory/profiles, daily requirements including setup days, pending requests, confirmed roster and direct-add form were inspected without changing Base44 records. Its save/approval backend and a staff-only Base44 session were not exercised.

Verification uses clearly SYNTHETIC data. Focused acceptance label is apps.staffing.tests (68 tests); a package-only label encounters namespace discovery behavior and is not the acceptance selection. Normal full regression acceptance includes existing Build 8/9B tests plus staffing tests. Populated migration verification preserves Event, participant, registration, QR token/expiry and ExperienceSession IDs; migration creates no staff identities or permission grants. Desktop/mobile browser tests use manager and general-staff roles; no participant records or private manager notes are exposed to the general role.

No tasks, checklists/templates, payroll, attendance, expenses, reports, external messaging, production storage changes or full disconnected synchronization are included. Build 9D/9E remain separate approved work.

Acceptance rerun after Codespace resume: 68 focused tests passed; full regression 477 tests passed with one existing skip. Django system checks, migration drift check and whitespace validation passed.
