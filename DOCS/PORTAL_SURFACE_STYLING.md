# BTW portal surface styling standard

Preferred for future BTW staff and customer portal development:

- Light-gray page backgrounds using the existing `--page` token.
- White cards and content surfaces using `--surface` and the shared `.card` component.
- The existing subtle card shadow for depth; avoid heavy shadows, gradients, and decoration.
- Consistent rounded corners using `--radius`, with established component spacing.
- Neutral gray section dividers, with BTW red section headings using `--red`.
- Existing BTW typography, bundled BTW Futura Condensed display font, and branding.

Reuse `static/internal/css/tokens.css` and `static/internal/css/app.css`. The shared `.internal-app` provides the page background. The shared `.card` provides a white surface, a 1px `--line` border, `--radius` corners, and `0 2px 8px #1418200a` shadow. Do not duplicate or replace these foundations with competing tokens or broad overrides.

This standard is guidance for future work, not authorization to restyle existing pages. It does not apply globally to kiosk, simulator, public registration, or participant-facing experiences.

## Home Dashboard application

Home reuses the shared page and card surfaces while preserving its existing grids, spacing, responsive breakpoints, section headers, gray dividers, red headings, sidebar, and header. Quick Actions and Analytics already use `.card`; the notification preview now uses it too. Home-specific CSS retains layout/typography and retains Quick Action `--radius` corners and sets its border color to `--line` to match cards despite the shared `.button` rules.

No changes to Quick Actions preferences, editing, permission filtering, destinations, Updates behavior, or Analytics behavior are part of this styling refinement. Events, Participants, and other portal templates/styles remain unchanged.
