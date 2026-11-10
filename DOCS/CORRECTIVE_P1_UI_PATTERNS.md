# Corrective Pass 1 opt-in patterns

These patterns follow the approved UX-A reference. They do not replace domain services or change permissions.

- `workspace_ui.css`: opt-in `.btw-workspace-frame` provides a 920px content area and 24px desktop / 16px small-screen gutters. A deliberate `.btw-workspace-wide` exception is available for reviewed wide content. Events list, form and detail explicitly opt in with `has-corrective-events`. Expenses editors and other workspaces retain existing layouts.
- `internal/ui/record_row.html`: a native primary anchor, labelled by its heading, is a sibling of optional independent secondary controls. Use only developer-owned primary/secondary template names. Never put buttons, anchors, inputs or menus in the primary template. Browser keyboard, modified-click and open-in-new-tab behavior remain native; no synthetic article click handler is needed.
- `btw-detail-card` and lined `btw-event-facts` retain readable label/value relationships. Empty values say Not recorded rather than implying completion.
- `btw-section-nav` uses native anchors. `workspace_sections.js` opens a matching event form disclosure for a fragment; native summaries remain usable without JavaScript. Bound field errors open optional groups. Existing model fields, validators, timezone conversion, revisions, draft/publish and return validation remain authoritative.
- Seven staff destinations are assembled from existing workspace policies. Old workspace URLs use the unchanged legacy policy resolver; settings groups reuse existing destination guards. This does not authorize a new tool or customer feature.
- Station configuration uses existing permissions and service endpoints in Event Operations. Recorded experiences and scores remain in Results; the old station fragment points to Operations. No formulas or health status logic are changed.

Verification and visual acceptance are separate. Browser automation and developer checks do not count as staff usability testing or owner approval.
