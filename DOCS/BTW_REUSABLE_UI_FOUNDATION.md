# BTW Reusable UI Foundation — Expense reference implementation

UX-A local review candidate. Extends existing Django templates, inclusion tags, native HTML and BTW tokens. No frontend framework or dependency was added to the application. Adoption is opt-in; unrelated screens have not been converted.

## Existing patterns retained

- Event Workspace event identity, tabs and branding.
- ui_field / internal/ui/field.html: labels, required markers, errors and accessible descriptions; no POST-name changes.
- internal/ui/form_errors.html and ui_empty: existing error summaries and empty states.
- Native dialog approach demonstrated by Add Task. Its task-specific lookup and creation controller remains unchanged.
- Existing button classes, disclosures, icons and tokens.css spacing, color, radius and typography variables.

## Implemented reusable patterns

| Pattern | Location | Contract / adoption |
| --- | --- | --- |
| Compact editor shell | templates/internal/ui/record_editor_dialog.html, editor_panel.html, editor_actions.html | Include one dialog per adopting page. Extend the panel content block. Supply title, optional context, canonical action URL, Save label and validated Cancel URL. Required fields stay outside optional disclosures. |
| Progressive editor lifecycle | static/internal/js/record_editor.js | Opt in with same-origin links carrying data-record-editor. Native dialog containment, focus, Escape/Cancel/close, discard confirmation, focus restoration, browser Back, loading, busy/duplicate-click protection and retained retry values. Native links/forms provide the fallback. |
| Compact fields/actions | static/internal/css/record_ui.css: btw-editor-grid, btw-editor-wide, btw-editor-mobile-wide | Reuse ui_field. Readable inputs, 44px actions, scrolling and sticky actions sized to the available visual viewport. |
| Responsive record list | btw-ui btw-list-panel, btw-compact-table, btw-row-actions, btw-list-total | Semantic table with opt-in compact styling. Column headers and data-label attributes support labeled mobile cards. Domain code supplies values, permissions and calculation meanings. |
| Details / History | Existing btw-disclosure plus btw-secondary-details / btw-record-facts | Native details/summary, labeled facts and History links. Keep accounting meaning and excluded/voided states available. |
| Feedback/recovery | Existing error summary plus btw-feedback, data-editor-recovery, data-editor-status | Focus errors/recovery. Never silently refresh a revision. Network failures retain fields and request identity for safe retry. |

## Endpoint contract

X-BTW-Editor: 1 changes presentation only. Every endpoint must retain normal login, permissions, CSRF, validation, idempotency, audit and concurrency checks.

- GET: trusted server-rendered data-editor-panel fragment plus X-BTW-Editor: 1 response header.
- Valid POST: retain server success feedback and return JSON containing a validated internal return URL, with the same response header.
- Invalid POST: bound fragment at 400; conflict at 409. Preserve original submitted revision and request key.
- Ordinary requests: complete page / redirect flow.
- Unrecognized responses and network failures never count as saved. Same-origin validation in the browser supplements mandatory server return validation.
- Place data-editor-autofocus on the initial field wrapper. data-editor-reload retrieves a fresh fragment only after an explicit user decision. data-save-blocked prevents stale retries until recovery.

## Expense-specific adoption

expense_editor_panel.html supplies required expense fields, optional visible Notes and accounting help. New expenses need no staff reason; a standard creation audit label satisfies the existing storage contract. Edits and voids retain required reasons. Reference is no longer an entry control; existing values remain in Details/History and survive omitted edit submissions. This is the owner-authorized notes simplification. expense_context.py validates same-event return context. expense_form.css only suppresses the duplicate contextual heading on adopting expense pages. The prior expense-only script is a compatibility stub; templates load the shared controller. Earlier source and review evidence remain preserved. Financial services and models are unchanged.

## BTW Reusable UI Foundation — Recommended Adoption

These are future proposals, not implemented conversions or authorization to expand UX-A.

- UX-B: short planning/staffing actions can adopt compact editors. Keep complex decisions on appropriate full pages with staffing permissions and revisions.
- UX-C: adopt resource-list density, labeled actions and Details/History. Navigation restructuring requires its own scoped build.
- UX-D: use for short authorized participant edits. Intake wizards, sensitive comparisons and extensive profiles retain appropriate full-page patterns and identity/privacy safeguards.
- UX-E: reuse filters, labeled totals and detail access. Preserve reporting populations, incomplete values and authoritative calculations. Editors do not replace dashboards.
- UX-F: reuse known/unknown/stale/conflict feedback hierarchy. Connectivity instrumentation and monitoring technology are not included.

## Verification and limits

Automated checks cover legacy expense workflows, fragment/JSON responses, permissions, CSRF, exact financial totals, audit history, stale edits, filter context and the existing Task dialog frontend contract. Browser evidence uses isolated synthetic records. Physical keyboards, screen readers and uncoached staff trials remain separate; viewport resizing is not on-device validation.

## Integration reconciliation

Review component names and endpoint contracts before later adoption. Add Task has a specialized API/lookup controller; do not switch it merely by changing classes. Reconcile concurrent expense view/form/template edits and presentation assertions before integration. Build 11/11B acceptance and the migration handoff remain separate. No commit, merge, rebase, push or deployment is part of UX-A.
