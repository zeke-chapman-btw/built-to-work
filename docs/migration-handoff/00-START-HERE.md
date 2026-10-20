# BTW migration handoff — start here

Prepared October 20, 2026 (America/New_York); verification began October 19 and crossed into October 20 UTC. Documentation-only snapshot after Build 9F. Review status: **draft, uncommitted**.

## Continuity contract

This is a ChatGPT Business workspace/account transition, not a new software project. Continue the same repository, history, architecture, Codespace where access permits, and scoped-build workflow. A new account is not a reason to rebuild, refactor, redesign, move hosting, replace tools, or reset databases. Do not begin Build 10, Build 9G, or any other implementation until the owner approves a concrete scope. Do not commit, push, merge, or deploy these handoff documents without explicit approval.

## Application and verified checkpoint

Built to Work (BTW) is a Django platform for event operations, staff scheduling and compensation records, participant registration and QR ticketing, kiosk assessments/games, accepted simulator results, participant evidence/review, and event cost/performance reporting. Customer/recruiting/CRM ambitions extend beyond the implemented portal; see the feature inventory.

| Item | Verified state |
| --- | --- |
| GitHub repository | https://github.com/zeke-chapman-btw/built-to-work |
| Active development worktree | /workspaces/btw-build-9f in the existing GitHub Codespace |
| Active branch | build-9f-event-reporting |
| HEAD / completed 9F commit | 01e48c2bc0adcac0a5047291791cdda40cbe5328 |
| Previous 9E baseline | cb83021ec5176b08660054ee03fea2729308abfa |
| Cached origin/render-preview and origin/build-9f-event-reporting | Both point to the 9F commit; no fetch/push performed for this handoff |
| Preview | https://btw-preview.onrender.com/internal/ |
| Reporting | https://btw-preview.onrender.com/reports/ |
| Codespace | symmetrical-funicular-qvpxx6rx5j7wc96wv; https://symmetrical-funicular-qvpxx6rx5j7wc96wv.github.dev/ |
| Fresh checks | Full suite 678 run, OK (skipped=1); Django check passed; no migration drift; health returned status ok |

The VS Code Explorer opens **/workspaces/built-to-work-build-8a**, an older, dirty worktree on local render-preview. Its folder title and status bar do not describe the terminal's 9F worktree. Always verify pwd, branch, HEAD, and status explicitly. Local main and the cached origin/main are at Build 6A, not 9F. Do not start from default main by assumption, pull into the old dirty preview checkout, or reset it.

## Uncommitted work that must survive

Before handoff creation, 9F had no tracked modifications but **one untracked terminal/pager artifact** with a long filename beginning e64;exec(base64.b64decode(. It was discovered during this audit and left unchanged. It is 13,067 bytes, SHA-256 f1aaab94ce30f41c2c6aa8a898f9ad4eada8dccc9eaec8cdc86909d3d4a22aa6. The earlier delivery's clean-tree statement must be read with this correction. Do not include this artifact in a future commit by using git add . .

This handoff adds only docs/migration-handoff/*.md. Other dirty worktrees contain separate simulator and UI work; [setup/recovery](06-SETUP-AND-RECOVERY.md) inventories them. A GitHub clone does not recover uncommitted or ignored files, local databases, media, virtual environments, terminal buffers, or private configuration. No such files or secrets are bundled here.

## Read next

1. [Architecture](01-ARCHITECTURE.md) and [feature inventory](02-FEATURE-INVENTORY.md).
2. [Build history](03-BUILD-HISTORY.md) and [current status / Build 10](04-CURRENT-STATUS-AND-BUILD-10.md).
3. [Data/integrations](05-DATA-AND-INTEGRATIONS.md), especially the separate 6B XML branch.
4. [Setup/recovery](06-SETUP-AND-RECOVERY.md), [decisions/open questions](07-DECISIONS-AND-OPEN-QUESTIONS.md), and [starter prompt](08-NEW-CODEX-STARTER-PROMPT.md).

## First new-session action

Authenticate independently to GitHub and verify repository permission and Codespace ownership/access. Inspect these documents and actual source at the current HEAD. Compare remote branches with this snapshot without altering worktrees. Report discrepancies, uncommitted work, current test evidence, access gaps, and a proposed next scope. Wait for owner approval before modifying application code. Workspace membership does not itself prove GitHub, Render, Base44, document, hardware, or secret-manager access.

This package deliberately contains no credentials, session tokens, database dumps, candidate/employee/customer records, or real private files. It is a project context package, not a complete environment or data backup.
