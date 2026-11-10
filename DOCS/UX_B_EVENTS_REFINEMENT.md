# UX-B — Events Experience Refinement

Local review implementation on `ux-b-events-refinement`, based on combined checkpoint `7c078c4dfd9699e525be8d07ea529ee74b0a00bd`. This checkpoint preserves the 19 reviewed UX-A files over published checkpoint `8b48e01f50597a78f48687dd98dba3de8de249aa`. UX-B source remains uncommitted pending owner review.

## Journeys and existing patterns

The event discovery list, Info, setup/scheduling, manager staffing, staff requests/schedules, event Operations, Results, registrations/tickets, public settings, tasks, notes and documents adopt the reviewed UX-A record UI. The familiar Event Workspace and BTW shell remain the foundation.

- Lists retain search, status views, pagination and authoritative counts. Active filters have individual removal links. Per-tab discovery context restores the originating event list and its scroll position where practical.
- Complex event setup stays on a full page. Essential dates precede optional logistics; advanced groups retain every existing field and expand for errors or saved settings. Timezone suggestions do not restrict existing valid named timezones. The existing timezone/DST and schedule validation services remain authoritative.
- Empty Other-type details appear when Other is selected. A nonempty retained detail remains visible with an explanation when another type is selected, to avoid silently hiding or discarding saved data. Without JavaScript the original explicit optional control remains available.
- Short staffing/lifecycle actions reuse the shared native dialog, bound field/error patterns and full-page fallback. Staffing calendars longer than seven days retain full-page entry links.
- Daily coverage continues to use the staffing service. Assignment counts in discovery are labeled as assignments and link to daily coverage; they are not compared with default daily staffing needs.
- Event resource setup uses compact rows and secondary Details, preserving configuration states and the distinction from physical readiness. Existing specialist tools remain reachable. General resource libraries, monitoring technology and identity/intake workflows are not redesigned.

## Shared component contracts

`internal/ui/event_assets.html` adopts `record_ui.css`, `event_refinement.css` and `record_presentation.js` only on the intended event surfaces. Non-event Operations, Consents and staff-directory flows retain their existing presentation. The Event Report does not inherit the new form styling. Expenses keeps its own reviewed editor/dialog and list styling.

`internal/ui/record_form_panel.html` extends the existing UX-A `editor_panel.html` and `editor_actions.html`; it is not a second editor system. Endpoint adapters retain their original authorization, CSRF, forms, transactions, domain services, revision checks, consequential reasons and audit behavior.

`apps.internal_ui.record_editors` validates exact same-event/list paths and a finite query allowlist. It rejects external/protocol-relative destinations, backslashes, control characters, unknown paths/keys and fragments. Return context never grants permission. Fragment GET/error responses acknowledge `X-BTW-Editor: 1`; successful saves return the server-validated URL. Native requests retain redirects and existing full-page error status contracts.

Stale forms retain submitted values, disable obsolete saves and offer explicit reload/current-record recovery. An assignment removed or replaced by another session returns the entered values with a route to current staffing records instead of losing them in a 404. Domain services still reject the unavailable action.

`record_editor.js` preserves clicked submitter names/values, guards repeated initialization and retains the existing focus, dirty-discard, Escape/Back and loading behavior. Uncertain saves use record-check guidance by default. Only the existing expense endpoint opts into its proven request-key retry guidance; event/staffing creation is not represented as idempotent.

`record_presentation.js` enhances opt-in forms/tables/conditions only. Native multi-action forms keep the clicked action when controls become busy. Simple explicitly marked tables gain labeled mobile cards; dense/spanned tables retain their accessible table fallback. Optional conditional text fields keep nonempty values visible. Clipboard actions report success or offer manual selection.

## Preserved boundaries

No models, migrations, financial/staffing calculations, reconciliation, compensation, registration/ticket services, permissions, identity/intake logic, audit policies, application configuration or global navigation are changed. Mandatory staffing, lifecycle and financial correction reasons remain governed by their existing services. General Operations/Consents navigation remains intact.

Event content retains the existing 920px workspace and approved 620px UX-A dialog rather than adopting new global width tokens. The existing shared Add Task controller remains independent. General reports/monitoring and linked specialist systems retain their established meanings and ownership.

## Verification and owner review

Focused and complete Django suites, system/schema checks, JavaScript syntax, synthetic browser task walkthroughs and before/after captures are recorded in the UX-B review evidence package. Review covers desktop/mobile widths, permissions, native fallback, validation, stale/replaced records, save/cancel, financial exclusions and Add Task compatibility.

All browser forms target loopback servers and disposable synthetic databases. The before/after visual database is separate from the database used for task mutations. Review fixtures, browser dependencies and temporary settings live outside the application worktree.

Developer verification does not constitute an uncoached staff usability trial, screen-reader certification, physical mobile-keyboard testing, trailer-hardware performance measurement or offline certification. Owner review and the approved staff usability release gate remain outstanding. No deployment, push or further integration is included.


## Owner Event Info refinements

Event Info uses lined rows, with adjacent Start and End rows containing their date and time together. Event code is absent from staff discovery, Info and the event editor; existing stored codes remain intact and are no longer editable through this form. The extra daily-planning-hours Info row is removed. Existing daily staffing defaults and per-assignment scheduling controls remain available and unchanged; these are availability/overlap settings, not clocked work or hourly pay. Attendance and daily pay logic were not changed. Existing duplicate attendance protection is scoped to staff/event/local work date, not a new platform-wide cross-event restriction.

Time zone selection uses familiar Eastern/Central/Mountain/Pacific/Arizona/Alaska/Hawaii labels while retaining all additional supported named zones. Values remain IANA names; display abbreviations follow the relevant date, including EST/EDT and PST/PDT. Events spanning a clock change show both abbreviations. Existing DST rejection, timestamp precision, conflict checks and audit history are preserved.
