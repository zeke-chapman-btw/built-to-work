# Event workspace UI patterns

Status: implemented review candidate on `ui/event-workspace-completion`, based on `0ae49d2391864b0d76bb1e66eca3a514a6ed4c3e`. Owner visual acceptance and GitHub/Render release are separate steps. This document does not authorize applying the patterns to additional application areas.

## Boundaries

Use the existing internal application navigation and shell. Event content keeps a common 920px rail; focused forms use a 760px maximum and lifecycle confirmations use 640px. Keep primary and secondary navigation aligned. Operations Templates use an explicit internal resource scope because their list and revision editors also exist outside a particular Event.

The new stylesheet is loaded only by the internal Event shell or the Operations Templates wrapper. Its selectors require `internal-app` plus `has-event-context` or `has-event-resource-context`. Do not add it to public registration, kiosk, game, simulator or station runtime shells.

The actual kiosk screens, scan flows, questions, game behavior, simulator integration, offline behavior, compensation calculations, financial sources, lifecycle rules and authorization services are unchanged. Event Quizzes & Tests changes affect the internal configuration page's presentation only.

## Reusable components

| Pattern | Source | Use |
|---|---|---|
| Bound field | `apps/operations/templatetags/ui_patterns.py:ui_field`, `templates/internal/ui/field.html` | Explicit label, required indicator, retained value, linked help/errors; supports checkbox/radio groups. An optional DOM ID never changes the submitted field name. Pass an actual BoundField; guard absent forms before calling. |
| Form fields | `templates/internal/ui/event_form_fields.html` | Includes errors, retains every hidden field and renders visible fields through ui_field. The pre-existing global form_fields.html is preserved. |
| Error summary | `templates/internal/ui/form_errors.html` | Show non-field errors and field links; include hidden-field errors in the summary. Keep errors outside collapsed optional sections. |
| Form actions | `templates/internal/ui/form_actions.html` | Explicit Cancel destination and the caller's submit label. Preserve action names/values where a form has multiple submit operations. |
| Section heading / empty / badge / metrics | Existing `ui_panel_heading`, `ui_empty`, `ui_badge`, `ui_metric` tags | Reuse the established library instead of creating a new card style per page. |
| Coverage / date selector | `event-day-tiles`, `event-day-selector` in `static/internal/css/event_workspace.css` | Show daily Staff and Driver needs only when requested; filled role lines and fully fulfilled days turn green. No-needs days stay neutral; requests never fill coverage. Date links keep an accessible selected state. |
| Record / disclosure | `event-assignment-item`, `event-record-actions`, `btw-disclosure` | Show identity, state and useful actions first; retain audit/provenance underneath. Auto-open optional form sections containing errors or existing values where applicable. |
| Data entry / table | `event-entry-grid`, `btw-table-wrap`, `btw-table` | Pair short related fields; contain wide tables inside their own horizontal scroll area; keep totals' scope explicit. |

Do not replace a confirmation with a generic Save if that loses the action's consequence. Preserve reason, confirm, revision, expected-ticket and request-key fields. A display component must not decide authorization or silently introduce business defaults.

## Standard review sequence for the next area

1. Inventory every route, tab, secondary tab, More entry, form, action, history page, empty state and permission-restricted path. Identify external/global tools separately.
2. Compare the actual Base44 reference screens and the actual current application. Record evidence and differences; distinguish a visual pattern from a business rule or missing capability.
3. Agree scope and consequences. Identify unchanged public/runtime boundaries and any work needing a separate owner decision.
4. Reuse the internal library and implement actual page structure, not only a broad CSS layer. Keep backend services and existing data contracts authoritative.
5. Verify layout at normal desktop size, 768px tablet and 390px mobile. Check long labels, optional details, errors, populated/empty states, local table/tab scrolling, keyboard focus and action-strip clearance.
6. Run affected workflow/security regressions, record checks and skips, compare protected worktrees, and present screenshots plus an honest checklist for owner review. Commit, push and deployment require their own applicable authorization.

## Acceptance checklist for each surface

- [ ] Correct Event identity, active navigation and useful return destination.
- [ ] Clear primary action; secondary actions and consequential actions remain distinct.
- [ ] Consistent width, spacing, typography, section headings and empty state.
- [ ] Existing permissions still govern visibility and every endpoint.
- [ ] Field names, hidden guards, CSRF, values and server validation retained.
- [ ] Reason, confirmation, history, snapshots and audit attribution retained.
- [ ] Financial/count/date/timezone labels retain their actual scope and meaning.
- [ ] Optional details remain accessible; errors and current values are not accidentally hidden.
- [ ] No unintended page-level horizontal overflow; tables and tabs may scroll locally.
- [ ] Keyboard navigation, visible focus and dialog Escape/focus return checked.
- [ ] Public/kiosk/runtime assets and behavior unaffected.
- [ ] Source, automated results, browser evidence and owner acceptance recorded separately.

## Presentation-only backend adjustments

- Staffing groups the existing paginated assignment page into confirmed and retained-history lists. The query, pagination, services and coverage calculations are unchanged.
- Financial correction history includes its already-authorized Event in presentation context.
- Note/document forms carry their already-authorized Event and active section.
- Event Tasks supplies a return path to its own task list. The return-path allowlist accepts only the exact existing local Event Tasks route, strips extra query/fragment data and retains the destination's normal permission checks. It rejects external URLs and other Event sections. Global Tasks remains the task detail/editor workspace.

## Explicitly deferred

An Event-specific game configuration editor, any global Django Admin redesign, real private document storage, public/kiosk shell changes, expanded offline/hardware work and readiness/exception policy are not introduced.

The owner wants a future admin place to view kiosk screens in a static state, and may later want easier kiosk text editing. Both are future work only; no viewer, editor, route, navigation item, model or setting is implemented by this pass.

Group/answer corrections continue through existing administrative workflows. Their dedicated internal UI requires a separate scope decision. Existing report/export, printing and simulator/offline entry points retain their established workflows.

See `EVENT_WORKSPACE_REVIEW_STATUS.md` for current verification and remaining acceptance work.

## Approved daily role coverage and final cleanup

The owner approved two coverage categories: Staff includes Staff and Staff Lead; Driver includes Driver and Combined (Driver + Staff). One confirmed person fills one category per day. Combined never also fills a Staff slot.

Only requested categories appear. Each filled role has green text and an explicit Filled label. A day becomes green only when every requested role is fulfilled. No-needs days stay neutral. Unknown legacy roles are flagged for review and excluded from role fulfillment; pending, replaced and removed assignments do not fill needs.

This follow-up includes a deliberate staffing data extension: required_drivers on StaffingPlan and DailyStaffingNeed, and coverage_role on StaffAssignment. It does not change Workday roles, compensation rates, per diem, mileage calculation or payment approvals.

Migration staffing.0002_daily_role_coverage keeps original responsibility text and daily needs. It maps exact known role labels only, leaves ambiguous/blank roles for manager review and initializes driver needs to zero. Existing general headcount remains in required_staff, now the Staff requirement; review older plans when splitting Staff and Driver needs. The populated upgrade test verifies retained assignment dates, text and needs.

Plan edits and role revisions continue through the existing permission checks, event/staff locks, revision guards and audits. Role corrections create a new assignment revision. Availability approval exposes the planning role without modifying payroll roles.

Presentation patterns added: event-coverage-role, coverage-complete, event-role-badge, event-needs-grid, event-icon-action, event-row-actions and event-roster-table. Icon controls need explicit accessible names and visible focus. Expense row disclosures keep correction, void and history links; do not remove their permission checks.

Current Base44 Staff was reviewed read-only as a visual reference. The global internal shell remains the BTW unified shell. Public/kiosk/game/simulator runtime screens remain outside these selectors.

Final verification: full SQLite suite 993 run, 988 passed, 5 skipped; final affected-workflow suite 308 passed; all five existing Node scripts passed. PostgreSQL, exhaustive accessibility and live hardware certification are not claimed. Owner visual acceptance remains pending.
