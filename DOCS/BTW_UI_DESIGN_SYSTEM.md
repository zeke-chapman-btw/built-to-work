# BTW UI Design System

Build 10C establishes reusable presentation patterns within the existing Django architecture. This is an ongoing application, not a new frontend framework. Official logo assets, brand colors, typography resources, permissions, domain services and named URLs remain authoritative. No dependency or tracked deployment configuration change is required.

## Reference and ownership

The actual authenticated BTW Base44 interface was inspected on 2026-10-21: Events listing/status navigation, event header and Info, Staff coverage/assignments, Clock, Expenses, the expense modal, Tasks, Notes and Event Report. Desktop and mobile presentations were observed. See BUILD_10C_BASE44_AUDIT.md for evidence and limits. Base44 guides layout/workflow; Django governs calculations, authorizations and data semantics. Do not copy unavailable Base44 features into Django as placeholders.

Shared CSS: static/internal/css/btw_ui.css, loaded after existing internal styles by templates/internal/base.html. Shared presentation tags: apps/operations/templatetags/ui_patterns.py (the existing installed operations app owns template-tag discovery; internal_ui is not added to INSTALLED_APPS). Shared templates: templates/internal/ui/. Public, kiosk and participant shells do not load the library.

## Tokens and application layout

| Concern | Pattern |
| --- | --- |
| Branding | Existing official linear logo and black/red shell; existing --red, --dark, --ink, --muted, --surface and --line tokens. Never replace these with a framework theme. |
| Typography | Existing --font for body, data and record titles. Existing branded display font remains for workspace headings. Record title: 24px bold, 22px on mobile; section title: 15px; body: 13–14px; metadata/labels: 11–12px. No external font dependency is added to reproduce Base44's Roboto. |
| Spacing | --btw-space-xs 4px, sm 8px, md 16px, lg 24px, xl 32px. Cards generally use 16px padding and 12–16px gaps. |
| Surfaces | --btw-radius 10px; thin existing border; --btw-shadow for restrained card elevation; --btw-shadow-menu for disclosures. |
| Controls | --btw-control-height 38px on desktop; primary interactive controls at least 44px on mobile. Focus uses --btw-focus-color with visible outline. |
| Content | --btw-content-width 1320px maximum within the existing sidebar/content shell; fluid and min-width:0. Primary desktop review target is approximately 1440px. |
| Breakpoints | 900px for compact record actions; 640px for stacked fields/cards and touch targets. Reuse existing shell drawer behavior. |

Event workspaces retain the shared header and a compact primary navigation row. Lower-frequency Tasks/Notes/Documents stay discoverable in a native More disclosure. Link navigation uses aria-current=page and a red underline plus weight; do not use ARIA tab roles for server navigation. Status tabs use text and underline, never color alone. All existing deep links and endpoint permissions remain.

On Event pages, one floating Add Task action uses the existing modal/fallback and permission-checked event context. A reserved footer action area and independently scrolling event content prevent the action from covering forms/tables. Do not duplicate it in the record header. Do not introduce floating actions whose links bypass domain authorization.

## Reusable components

Load ui_patterns in the template using the tags. Inclusions must load their own custom filters/tags rather than assuming a parent load applies.

- ui_panel_heading title subtitle action_url action_label: compact heading and optional authorized action; omit action if either URL or label is absent.
- ui_empty title detail action_url action_label: meaningful empty state, with optional real destination. Do not imply missing data is an error or insert dead buttons.
- ui_badge label tone: neutral, info, success, warning or danger. Tone is allowlisted. A state label must have an actual domain definition; configuration does not become readiness because it is green.
- ui_metric label value detail: actual zero stays zero; None is Unavailable. Supply source/scope in the detail and retain original calculations.
- ui_tabs items selected label variant: route links with id/label/url data. Variants primary/status/secondary; selection has accessible current-page markup.
- internal/ui/form_fields.html: includes nonfield errors, hidden fields, visible labels, controls, field errors and help text. Caller owns form action, method, CSRF and transaction/business rules.
- events/event_header.html and event_tabs.html: canonical event identity, management actions and permission-aware sections; event_ui tags compute actual links.
- events/staff_navigation.html: existing Assignments & coverage and Workdays & mileage destinations, each shown only under its existing complete authorization predicate.

Supporting CSS classes: btw-page, btw-stack, btw-grid, btw-panel, btw-panel-heading, btw-toolbar, btw-badge, btw-metrics/btw-metric, btw-table-wrap/btw-table, btw-fields/btw-field, btw-disclosure, btw-inline-notice and btw-record-list. Prefer these over copied page-specific CSS.

## Page patterns

| Page | Guidance and current example |
| --- | --- |
| Lists | Compact heading/toolbar, primary action before search, disclosure filters with clear/reset inside, text status tabs, records with readable metadata, real pagination/empty states. Events listing is the reference. Preserve query parameters and validation. |
| Record detail / event workspace | Shared identity/header then primary sections and optional More disclosure; grouped fact rows rather than scattered labels; existing management/lifecycle actions retained. Event Info is the reference. |
| Dashboard | Reuse panel headings and metric components; every metric states its actual scope. Preserve existing homepage behavior; Build 10C does not redesign the homepage. |
| Configuration | Explicitly labeled forms, current saved values, supported/unsupported states, existing editor links and separate consequential confirmation. Operations Templates/Setup remain authoritative. |
| Reports | Distinct report and expense presentation over the same reporting service; keep Preliminary/reconciled and unavailable/zero meanings. Management actions only with their existing grants; exports remain real server endpoints. |
| Management tables | Semantic headings and column labels, internal overflow wrapper, compact rows, grouped actions. Avoid a document-wide horizontal scrollbar. Station assignment/Results tables are examples. |
| Forms | Labeled controls, visible validation errors, clear submit/cancel, preserved CSRF/stale-edit checks and history. The native Event date picker displays milliseconds where needed. Unchanged values preserve the exact server-stored timestamp, including finer precision and known DST fold; legitimate changes retain existing validation. Do not round stored schedules as a cosmetic operation. |

## Dialogs, confirmations and interaction

The existing operations/task_dialog.html and task_create.js remain the functional modal/fallback implementation: native dialog, keyboard focus containment/return, Escape/close, duplicate-submission protection and validation/retry. Reuse this established architecture for future dialogs rather than inventing a competing modal controller. Styling uses the existing branded button hierarchy, modest radius and clear focused control. A modal is a presentation choice, not authorization to create a new mutation endpoint.

For consequential confirmations, retain the established server form, explicit confirmation, revision token/reason where required and CSRF protection. Do not perform mutations on GET. Existing event lifecycle and template confirmation pages continue their established behavior.

Native details/summary menus work without JavaScript. ui_patterns.js adds Escape closure with summary focus return and outside-pointer closure for data-ui-menu disclosures. Navigation lists are not ARIA menus. Do not add menu roles without implementing their full keyboard contract.

## Process for future builds

1. Inspect the actual repository, this library and the closest current page pattern before designing a screen.
2. Determine domain permissions, existing named routes and data semantics before choosing presentation components.
3. Reuse the tags/templates/classes above. If a new workflow lacks a pattern, inspect the actual Base44 reference, document the observation and add a narrow reusable pattern instead of guessing.
4. Preserve existing accessibility, keyboard/focus behavior, official/test separation and responsive shell behavior. Keep implementation details out of user flows unless needed to make a meaningful decision.
5. Run relevant focused/full tests and frontend checks. Review real synthetic-data screens around 1440px plus tablet/mobile; inspect overflow, links, selected states, validation, floating action clearance and modal focus.
6. Record screenshots, what was actually inspected, limitations and source paths. Owner authorization for implementation does not imply permission to commit, push, merge or deploy. Keep protected worktrees and release workflow unchanged.

## Owner-reviewed Event reference refinement — 2026-10-21

The owner requested closer Base44 styling throughout Event content, including edit forms, menus and confirmations, while explicitly retaining the new application navigation and overall structure. Keep the existing sidebar, top bar, domain architecture, fields, services, permissions and named routes.

Event workspaces use a 920px maximum inside the existing shell; primary edit cards use 760px and confirmation content 640px. Info is a single stack of compact icon/label/value rows followed by contact and logistics. The header emphasizes identity/venue with a separate compact, timezone-correct date context; exact operating times remain in Info. Preserve date context on reports.

The ui_icon inclusion tag uses an allowlisted set of repository-owned SVG paths; icons are decorative, explicitly sized and hidden from accessibility names. Event navigation combines these icons with readable labels and aria-current. Native Event actions and More disclosures retain Escape/focus return; destructive actions still lead to the original confirmation form.

Event create/edit retains all original fields, hidden revision token, CSRF, actions and validation within one grouped form card and a sticky Cancel/Save bar. Staffing forms retain the Event header/context when available. Related operations, workday and expense edit forms use the shared btw-form-card class, scoped to Event context. Existing public-registration settings keep their real controls, using the same content styles. No Base44 map/geofence, staffing role, pay, housing schema or other missing workflow is copied.

The reserved Event task action area is reduced from 84px to 64px. Mobile review confirmed form actions end at 756px, the main content ends at 780px, and the floating task action starts at 790px in a 390×844 viewport. The footer action remains separate from form controls. Future Event UI work must cover edit states and confirmation screens as well as the initial record view.
