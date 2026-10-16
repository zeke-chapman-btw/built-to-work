# Build 8E-A staff workspace UI

## Navigation registry

apps/internal_ui/navigation.py is the single source for workspace identifiers, labels, order, named destinations, icons, descriptions, permission visibility, and workspace landing links. Add or reorder a workspace by editing WORKSPACES; use an existing named URL whenever possible. Add real, already implemented actions to WORKSPACE_LINKS. A label change does not rename a model or route. Removing a navigation item does not remove its underlying view.

The registry hides links when the user has none of the listed permissions. Superusers can see all configured workspaces. This is presentation only: each destination continues to enforce its own server-side authentication and permissions. Do not rely on navigation visibility for access control.

## Creating a workspace page

The shared portal frame is templates/internal/base.html. It supplies the skip link, persistent logo/navigation, responsive header, shared page heading/actions, content region, messages, and footer. Use templates/internal/workspace.html for a simple landing page with permission-filtered workflow cards, or create a focused page that extends internal/base.html.

Use page_title and page_description context values and the page_actions, workspace_tabs, and content blocks. Shared CSS components are defined in static/internal/css/tokens.css and static/internal/css/app.css: .page-header, .page-actions, .workspace-tabs, .workspace-tab, .card, .workspace-grid, .workspace-card, .empty-state, and .button / .button-primary.

Internal tabs should link to real named routes and be filtered by the relevant permission. Use aria-current="page" for the selected tab. Keep workflow authorization in its owning view.

## Styling boundaries

The staff frame loads static/internal/css/app.css only from templates/internal/base.html; selectors are scoped to .internal-app except the reusable portal font-face. The kiosk, public registration, game, station, and trailer templates do not inherit this frame. Do not add global element rules or edit kiosk styles when changing staff pages. The Events list is the first example of shared headers, action placement, tabs, cards, table overflow, and empty-state styles.

## Current workspace destinations

- HOME — /internal/
- EVENTS — existing event list at /events/
- PARTICIPANTS, OPERATIONS, ASSESSMENTS, ADMINISTRATION — permission-checked landing pages under /internal/workspaces/, with links only to implemented workflows that the current staff member may access.

No check-in, arrival, attendance, or Build 8E-B participant directory workflow is added here.
