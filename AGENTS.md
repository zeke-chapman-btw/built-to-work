# AGENTS.md

Standing instructions for every AI agent (Codex, Claude) and developer working in this repository. Read this file before every task. Where it conflicts with an older document in `DOCS/`, this file wins.

## Who uses this app

Built to Work (BTW) runs event trailers that connect people interested in the trades with employers. The internal app is used by BTW managers, event leads, and event staff and drivers. They are not developers.

They come to the app to see and act on **people, events, customers, staff, and numbers**: who signed up, who needs a call, which event is short on staff, what an event cost per lead. Every screen should put that information first. How the system stores, counts, filters, or audits data belongs in the code, not on the screen.

Event staff and drivers mostly use phones. Managers mostly use laptops around 1440px wide.

## Writing for the screen

These rules apply to every page, label, button, empty state, tooltip, and message a user can see, including text hidden with `sr-only`.

1. **No business rules or data-model notes on screen.** Rules such as "Registration does not prove attendance," "Approval confirms staffing, not attendance or pay," or "Missing dates are excluded, not inferred from record creation" stay in code, tests, and `DOCS/`. Do not show them as captions, helper text, subtitles, or "About this" panels.
2. **No disclaimers.** Never write "does not prove," "is not proof of," "does not change," "not confirmed," or similar defensive wording.
3. **Use the words BTW staff use.** Participants, customers, events, staff, drivers, days worked, leads, hires, touchpoints. Do not use system words such as canonical, records, retained, provisioning, accessible, scope, fixtures, identifier, classification, or audit source.
4. **Delete, don't hide.** When removing text, delete it from the template and the view. Do not move it into `sr-only` or a collapsed panel.
5. **Hide what a user can't use.** If a user lacks permission, hide the card or action. Never write "under your permissions" or "in your accessible records."
6. **One short subtitle per page at most** (under 12 words). No helper sentence under every card, tile, or table.
7. **Metric details show change or context, not scope.** A tile's detail line is something like "+4 this week" or "Last: Augusta, Oct 23." It is not a description of what was counted. This overrides the "every metric states its actual scope" guidance in `DOCS/BTW_UI_DESIGN_SYSTEM.md`.
8. **Empty states are one short line plus the action**, for example "No staff notes yet" with "+ Add note." No explanation of why it is empty.
9. **If a field genuinely needs explaining,** use one short sentence in a tooltip or info icon, not visible body text.
10. **Formatting:** sentence case for every label, button, heading, and tab. Money as `$1,250.00`. Dates as `Nov 7` (this year) or `Nov 7, 2025`. Times in the user's local time zone, never UTC. Phone numbers as `(706) 555-0142`, linked with `tel:`.

## Looking consistent

- Build with the existing shared foundation: `static/internal/css/tokens.css`, `static/internal/css/btw_ui.css`, `templates/internal/ui/`, and `apps/operations/templatetags/ui_patterns.py`. Extend these before creating anything new.
- Do not add a new CSS file for each page. Add shared styles to the shared files and keep page-specific CSS small.
- The same meaning always gets the same look. Use these badge tones everywhere:

| Meaning | Tone | Examples |
| --- | --- | --- |
| Yes, positive, attended, hired | success (green) | Seeking work: Yes, Attended |
| Maybe, in progress, needs review | warning (amber) | Maybe, Interviewing, Pending review |
| No, unknown, not checked | neutral (gray) | No, Not checked |
| Informational, verified, interested | info (blue) | Verified, Interested |
| Flag, overdue, problem | danger (red) | Overdue, Top performer flag |

- Primary buttons use "+ New …" for creating records. One floating "+ Task" button per page, never two.
- The participant ID is how BTW identifies a person to customers. Show it on the participant profile and in participant tables.

## Before you start a task

- Read the current state of the repository and any build notes for the area you are changing.
- Work in your own branch and worktree, branched from the latest `render-preview`. Never commit directly to `render-preview` or `main`.
- Preserve other people's work: other worktrees, branches, `recovery/*` branches, and uncommitted or staged changes. Stage files explicitly; never stage the whole workspace.
- Keep the change to the scope you were given. Do not redesign unrelated pages.

## Deployment

- `render-preview` deploys automatically to the preview site (btw-preview.onrender.com) on every commit, and runs migrations first. Merging into `render-preview` is a deploy.
- Do not merge into `render-preview` or start a deployment without the owner's approval.
- There is no production site yet. Anything production-related requires explicit owner authorization.
- After a merge, confirm the deployed commit and static assets match what was approved.
- Keep outbound email and SMS disabled in preview and tests (the preview uses the in-memory email backend).

## Data and code rules

- Use the existing domain services for reads and writes. Do not duplicate business logic in views or templates.
- Preserve audit and history. Record audit events explicitly with `record_audit`; never through model save signals. Void and replace records instead of editing frozen history.
- Enforce permissions on the server (views and querysets), not only by hiding buttons.
- Do not change calculations, payroll, permissions, or data meaning as part of a presentation task.
- Never put secrets or credentials in the repository.

## Testing

Run from the repository root with dependencies installed:

```sh
python manage.py test --settings=config.settings.test
node --test scripts/*.cjs
```

- Run the Django tests against both SQLite and an isolated PostgreSQL database when a change touches permissions, queries, exports, or migrations.
- When you change visible wording, update the tests that assert the old wording. Tests should check that information is shown, not lock in explanatory captions.
- A passing test suite does not prove a screen looks right. For any visible change, review the page in a browser at about 1440px and at phone width (390px).

## Reporting back

Every task ends with a short report:

- Branch name and commit.
- Every file changed.
- Every visible wording change, as old → new.
- Tests run and their results, and anything not tested.
- Anything left unfinished, with a one-line continuation note.

## Reference documents

- `DOCS/BTW_UI_DESIGN_SYSTEM.md`, `DOCS/BTW_UI_STANDARD.md`, `DOCS/BTW_REUSABLE_UI_FOUNDATION.md`: shared components and patterns (the writing rules above override them where they conflict).
- `DOCS/ARCHITECTURE.md`: locked technical rules.
