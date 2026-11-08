# Main landing workspaces

Tasks, Participants, Customers, Recruiting, Approvals, Events, Operations,
Reports & Analytics and Settings share `shared/workspace_landing.html`.
Operations supplies the visual reference. The shared frame owns the full available
main width, title/actions, optional selection, filter surface and result transition.
Use 24px desktop and 16px phone gutters, a 20px/28px title, a 16px header transition
and 24px section transitions. A stable main scrollbar gutter prevents lateral jumps.

Route selections use underlined links and `aria-current`. Task/Event view choices
remain filters with their existing counts and queries. Overflow belongs to the
selection row; the main page must not overflow horizontally. Pages without sections
have no fabricated Overview tab. Existing descriptions remain optional.

Only these landing pages load `workspace_landing.css`. Record editors, reporting
forms and portals retain their established frames and assets. Keep controller hooks
on the shared frame when they must encompass moved selections and filters. Preserve
GET field names, hidden values, permission conditions, saved views and return links.
Do not put a header create action inside a search form.

Before changing this contract, compare all nine together at 1920,1440,768,390 and
320 CSS pixels with identical sidebar/zoom/font state. Include desktop and phone
contact sheets, original captures, real DOM geometry, filter inventory parity,
keyboard disclosure/focus behavior and role checks. Run
`scripts/verify_workspace_geometry.py` against matched browser sidecars and
`apps.internal_ui.test_landing_consistency` against both supported databases.
These supplement visual review and the complete release regression gates.
