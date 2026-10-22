# BTW UI/UX Standing Standard

The owner approved the completed Event Workspace refinement as the authoritative internal application baseline. This applies to Build 10D and subsequent builds.

## Design authority

- Base44 is a visual/interaction reference.
- The approved BTW Event Workspace is the implementation standard.
- Existing BTW domain services, data, permissions and workflow rules remain the functional authority.
- Retain BTW branding, sidebar, top bar, navigation structure and audience-appropriate boundaries.

## Required scope for future builds

Every new screen and every substantially modified older screen must use the approved patterns, including its secondary forms, menus, confirmations, validation, empty/success/error states and responsive behavior. Do not place one modern panel inside a substantially changed but otherwise unreviewed legacy workflow.

Do not redesign unrelated sections without build authorization. Participant/customer navigation can suit its audience while retaining the visual language. Preserve exact kiosk runtime previews when fidelity is their purpose; document such exceptions.

## Reuse

Start with [EVENT_WORKSPACE_UI_PATTERNS.md](EVENT_WORKSPACE_UI_PATTERNS.md) and the actual source:

- `templates/internal/ui/` and `apps/operations/templatetags/ui_patterns.py`: bound fields, validation summary, field errors/help, panel heading, badges, empty state, metrics, tabs and icons.
- `static/internal/css/event_workspace.css`: approved scoped Event/resource presentation.
- `templates/operations/design_base.html`: common Operations wrapper in the existing internal shell; event-aware header/navigation.
- `templates/operations/platform_base.html`: Platform Tools navigation using that same wrapper.
- `templates/operations/list_filters.html`, `list_pages.html` and `apps/operations/presentation.py`: shared bounded list presentation; callers retain authorization and source querysets.
- `static/internal/css/operations_workspace.css`: small scoped extensions for checklist/progress and Operations-specific composition.
- Existing disclosures and `ui_patterns.js`: menu behavior, Escape/focus return. Existing shell/task-modal/notification behavior remains authoritative.

Use the same table wrappers and readable mobile rows, button conventions, form actions, field labels and status vocabulary. Extend an existing pattern before adding a new reusable one. Document purpose and exceptions. Do not add another component framework.

## Authorization and data

Presentation must not grant access. Hide unavailable actions and enforce the same rule on the server. Filter the authorized queryset before search/pagination. A primary Event Manager's scope is their event; it is not global financial, staffing, task or platform administration.

Preserve entered values and show bound validation errors. GET requests must not initialize checklist state, perform transitions or deliver notifications. Use existing domain validation rather than inventing readiness claims or new policies to fill UI space.

## Independent acceptance

Functional tests and UI compliance are separate requirements. Review all new/substantially modified screens and related interactions against the actual approved implementation.

Record desktop/mobile dimensions, populated and empty states, filters/sort/pagination, forms/validation, confirmations/disclosures, status and permission controls. Distinguish actual browser observations from automated role/render checks and unverified hardware behavior. Constrain layout grids and scroll wide tabs/tables inside their containers.

Each build completion report must include a **UI/UX Standards Compliance** section identifying reviewed screens, reused/extended components, responsive evidence, exceptions, inaccessible/unverified states and remaining inconsistencies. Do not infer visual completeness from a passing test suite.

## Delivery continuity

Read the current build documentation and repository/worktree state before changes. Preserve unrelated dirty work and protected branches/worktrees. Review explicit staged files; do not stage the workspace wholesale. Follow that build's authorization for commit/push, and obtain separate authorization for merging or Render deployment.

Build 10D's inventory and scope are in [BUILD_10D.md](BUILD_10D.md) and [BUILD_10D_SCREEN_INVENTORY.json](BUILD_10D_SCREEN_INVENTORY.json). Restricted Django Admin and participant/kiosk runtime exceptions do not authorize redesigning them in a later unrelated build.
