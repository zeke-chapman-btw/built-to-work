# Main landing workspaces

Tasks, Participants, Customers, Recruiting, Approvals, Events, Operations,
Reports & Analytics and Settings share `shared/workspace_landing.html`.
Operations supplies the visual reference. The shared frame owns the full available
main width, title/actions, optional selection, filter surface and result transition.
Use 24px desktop and 16px phone gutters, a 20px/28px title, a 16px header transition
and 24px section transitions. A stable main scrollbar gutter prevents lateral jumps.

All primary selections use underlined links and `aria-current`. Task/Event view choices
retain filter semantics with their existing counts and queries. Overflow belongs to the
selection row; the main page must not overflow horizontally. Pages without sections
have no fabricated Overview tab. All nine main-page title headers omit subtitles.
Keep meaningful explanations in the relevant content area, including the staffing
approval explanation.

Participant questions use native dropdown selectors. City / state is one text field;
each comma- or space-separated term must match the recorded city or state. No
geocoding or distance is inferred. Older city, state and trade-interest saved criteria
remain supported and visible as removable active filters, without new input controls.
Rows show name only in the first column, the existing numeric person record ID,
employment interest, effective travel willingness and Type, followed by a compact View.
Missing facts use a small grey dash with an accessible Not recorded label. UUIDs and
event-specific trailer codes stay in the profile. No numbers are allocated or backfilled.
Existing numeric person IDs take priority in search; #123 explicitly requests an ID.
Reviewed alias IDs resolve to the current canonical profile. Full phone search remains
available when the number is not an existing person ID.
Search, sort and all menu controls share one GET form and one Apply/Reset pair. Reset
clears the entire query. Phone disclosures contain the same single set of controls;
validation errors keep the disclosure open. Type editing uses the existing audited,
permission-checked editor, linked clearly from the profile and beside its Type value.
The participant portal has no direct Type editor.

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
