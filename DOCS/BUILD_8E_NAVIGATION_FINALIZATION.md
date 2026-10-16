# Build 8E staff navigation finalization

The staff shell now presents six direct, permission-aware workspaces: Home, Events, Participants, Operations, Customers, and Admin. Settings is a separate staff utility link at the bottom of the sidebar. Existing feature views remain responsible for backend authorization; hiding a navigation item is not an access-control boundary.

Events, participant, operations, assessment, and administration destinations reuse existing routes and permission checks. Quizzes and tests are reached from Operations. Customers is explicitly marked planned and contains no customer records or management actions. Settings shows the signed-in username and links to Django's existing self-service password-change form; it does not expose user administration.

The official linear logo SVG keeps its original artwork paths. Its viewBox was tightened to remove unused transparent vertical space, and the shared styles preserve intrinsic aspect ratio at desktop and drawer widths. Navigation uses the established --font-display token. The existing bundled font is static/kiosk/fonts/futura-condensed-pt-medium.otf. The staff shell loads app.css, which declares the BTW Futura Condensed @font-face and defines --font-display for navigation, with Arial Narrow, Arial, and sans-serif fallbacks. No locally installed Futura font is required.

The drawer remains a collapsible, layered navigation panel at tablet and mobile widths. No check-in, arrival, attendance, customer-management, or other not-yet-implemented workflow was added.
