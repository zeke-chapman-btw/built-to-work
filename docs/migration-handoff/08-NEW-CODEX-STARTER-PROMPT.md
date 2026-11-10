# New Codex starter prompt

Copy the following into the new Codex conversation. Provide this folder if it has not yet been approved/committed to GitHub; a clone will not include uncommitted documents.

---

We are continuing development of the existing Built to Work (BTW) Unified Platform after Build 9F. This is a ChatGPT Business workspace/account transition, not a greenfield project, rebuild, architecture migration, hosting migration or change of development strategy.

Repository: https://github.com/zeke-chapman-btw/built-to-work
Completed Build 9F: 01e48c2bc0adcac0a5047291791cdda40cbe5328
Previous Build 9E: cb83021ec5176b08660054ee03fea2729308abfa
Existing 9F branch/worktree: build-9f-event-reporting at /workspaces/btw-build-9f
Existing Codespace: symmetrical-funicular-qvpxx6rx5j7wc96wv
Preview: https://btw-preview.onrender.com/internal/

Start in read-only discovery. Read docs/migration-handoff/README.md and all nine numbered documents, then inspect the actual repository, any applicable AGENTS.md, README.md, DOCS/ARCHITECTURE.md and relevant build documents/source/tests. Use documents as a dated evidence map, not as permission to implement their future ideas. If the handoff is not in GitHub, ask me for the provided package rather than assume it exists.

I will handle new-account access. Verify available GitHub repository/Codespace access and tell me any concrete access gap; do not change ownership, credentials, permissions, tools or infrastructure to solve it. ChatGPT workspace membership does not transfer GitHub, Codespace, Render, Base44, document permissions or old conversation history automatically. Do not request or print secrets. Never include environment values, tokens, credentials, real candidate/employee/customer records or private files in reports.

Verify the actual working directory, branch, exact HEAD, cached and server branch state when accessible, worktrees and uncommitted changes without altering them. The VS Code Explorer previously opened /workspaces/built-to-work-build-8a on an older dirty local render-preview branch, while the correct terminal was in /workspaces/btw-build-9f. main was still at Build 6A in the inspected cached history; do not assume default main is the current app. Do not reset, clean, stash, delete, pull into or overwrite dirty worktrees. Preserve the untracked pager artifact and separate simulator/UI work unless I explicitly approve cleanup.

Important separate work: origin/build-6b-simulator-results at 2b4ae2b705825d6dbe16a3b4728387d163a48672 was not an ancestor of 9F. Its Windows XML monitor archives local results with scores Pending Formula and no Django delivery. Older uncommitted OCR/helper/calibration work remains in /workspaces/built-to-work. Do not silently merge or deploy either. Inspect identifier and integration contracts before any later integration.

Keep the existing Python/Django architecture, domain ownership, canonical identities, Django Groups/Permissions, explicit audit services, source financial snapshots, official/test isolation, private storage rules and server-rendered portal. Preserve the six primary sections and personal Settings gear. Do not add a parallel employee/participant/event/payroll/notes/task system. Current operational document uploads are disabled pending verified durable private storage. Actual mileage rate was left unconfigured rather than guessed. Staff compensation is true per-mile for trips, not Base44 fixed tiers.

Build 9F is complete for its development/preview scope: event expenses/categories, arbitrary distributed incentives, approved Workday/MileageTrip cost integration, financial reconciliation, official participant metrics, weighted cross-event reporting and four CSV exports. Recorded acceptance: 72 focused tests; 678 full tests with one SQLite/PostgreSQL-only skip; three migrations deployed Live. Handoff verification reran 678 tests successfully with one skip, Django checks and migration drift check, and confirmed preview liveness. Reverify safely where practical using isolated test settings/databases; never migrate or test against active real data. State which checks are fresh, historical, skipped or impossible. Do not equate process liveness with DB/provider/hardware readiness.

Home currently has functioning per-user Quick Actions, but Updates and Analytics are visual previews and no Home task panel is connected. Existing task management is implemented in Operations/event workflows. Future discussion mentioned compact Quick Actions and possible Tasks/Quick Actions/Updates/Analytics areas plus a visual UI/UX audit against authenticated Base44. This was not a final approved layout. Earlier documents called a comprehensive UI pass Build 9G; Build 10 scope/numbering should be discussed later. Do not begin Build 10 or 9G automatically. We are moving away from MoZeus; do not spend time reconstructing that integration. HubSpot is a future two-way communication/intake direction, not implemented functionality.

Preserve our established process: I provide scoped build briefs; you inspect actual state and reusable workflows, identify missing business decisions honestly, propose concrete scope, implement only after approval, test focused/full/check/migrations/diff with synthetic isolated fixtures, verify desktop/mobile and role/authorization boundaries, report exact changes/evidence/limits, and stop at the approved checkpoint. Approval for a prior build is not standing authorization for a new build. Report uncertainty rather than invent history, policies, prices, scores or completion.

For this first session, do not change application code, schemas, dependencies, settings, runtime data or handoff documents. Do not commit, push, merge, deploy or modify Render infrastructure without my explicit approval. Pushing render-preview triggers deployment. Do not publish documents merely to make them available under the new account.

After inspection, give me:
1. Verified repository, directory, branch, HEAD and differences from this handoff.
2. Uncommitted/separate work needing preservation.
3. Safe verification results and limitations.
4. Any concrete missing context/access questions.
5. The next proposed discovery/development step, with scope and acceptance criteria.

Then wait for my approval before modifying code or starting another build.

---
