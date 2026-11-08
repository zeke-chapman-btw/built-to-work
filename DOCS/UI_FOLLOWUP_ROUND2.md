# Round 2 workspace corrections

Scope: UX-015 through UX-018, residual UX-002/UX-010, DEF-001/DEF-002. This cleanup preserves domain behavior; the Builds 18–20 capability/backlog/owner-decision registers remain separate future work.

## Shared component contracts

- `static/shared/workspace_round2.css` follows the established shell/form styles. It applies to authenticated platform workspaces with explicit component variants. Native Django administration, public intake, kiosks and station interfaces retain their task-specific presentation.
- `btw-frame-workspace` uses available workspace width. `btw-frame-readable` caps readable editors at 920px. Heading, contextual Back/actions and editor share a frame; participant avatar offsets are intentional. Do not stretch every editor to table width.
- `btw-workspace-nav` is ordinary route navigation: BTW red underline, 44px target, visible keyboard focus, aria-current and local horizontal overflow. It does not acquire ARIA tab behavior merely because links look like tabs. `btw-workspace-navigation` keeps native More disclosures outside the scroll strip so menus are not clipped. Preserve existing role checks and destinations.
- `btw-filter-nav` is for query/status choices. `btw-inpage-nav`/section links jump within a page. Native details/summary retain disclosure behavior. Existing actual tabs retain their own keyboard implementation.
- Editor fields use 38px desktop controls; explicit `btw-compact-filter` controls use 36px. Phone controls use 44px minimums and readable 16px text. Multiple/size-based select listboxes keep intrinsic height. Existing shared border, error, disabled and focus treatment remains authoritative.
- Repeated opt-in classes intentionally meet legacy navigation specificity without blanket !important rules or changes to unrelated components.

## Directory and Customer editor

The Participant Directory uses full-width results, compact common criteria and Advanced groups. Phone filters start collapsed and expose active criteria/count. Search, sorting, all 71 controls, query parameters, saved views, counts, permissions and profile return context remain existing server projections. Import/Add stay together. CRM relationships follow matching directory results.

Customer Add a position uses one editor surface with seven field groups, native keyboard-focusable summaries and existing section controls. All 73 controls, hidden revision context, required-contact guidance, draft/submission validation, unsaved guard and contextual return remain. Equivalent Customer WorkforceNeed records display Positions; Workforce Requests, Campaigns and internal domain names remain distinct. Back remains inside the guarded form so the existing unsaved handler covers it.

Expense actions retain their prior static layout; Participant profiles retain continuous content. These are explicit regression safeguards.

## Verified current-feature defects

DEF-001: Operations warns that an uncertain ticket delivery may already have sent and retrying can duplicate it; operators should check the provider before retrying. Provider acceptance differs from confirmed recipient receipt. No transport, retry, permission, provider or notification policy changed.

DEF-002: Communications documentation states normal delays 2, 4, 8, 16, 32 seconds before the sixth-attempt limit and explains polling delay. The theoretical 1-hour cap is not reached by this normal attempt sequence. Code and attempt policy are unchanged.

## Review evidence

The dedicated owner package is outputs/ui-followup-round2/ in the shared project workspace. It contains scope/crosswalk, matched screenshots/measurements, actual-role and database/frontend/static/export evidence, preservation reports, limitations, deployment reconciliation and owner acceptance steps. Historical results remain separate from fresh Round2 results. Only a candidate passing all material release gates may enter render-preview. Production is excluded.

Viewport emulation does not establish physical-phone, soft-keyboard, true-zoom, screen-reader or print acceptance. Native unsaved-confirmation automation stalled in the browser tooling; do not claim that interaction passed. Source guard and automated coverage, where verified, are reported separately.
