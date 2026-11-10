# Built to Work — migration handoff

Documentation-only continuity package after Build 9F. **Uncommitted; ready for owner review, not approved for publishing.** This is a ChatGPT Business workspace transition, not a new application or development strategy.

## Documents

1. [00 — Start here](00-START-HERE.md): checkpoint, repository, environment and continuity rules.
2. [01 — Architecture](01-ARCHITECTURE.md): stack, domains, authorization, routes, hosting and configuration names.
3. [02 — Feature inventory](02-FEATURE-INVENTORY.md): implemented/verified vs partial, planned or not found.
4. [03 — Build history](03-BUILD-HISTORY.md): Git evidence through 9F, separate 6B history, verification and working process.
5. [04 — Current status and Build 10](04-CURRENT-STATUS-AND-BUILD-10.md): final 9F acceptance, debt and deferred planning.
6. [05 — Data and integrations](05-DATA-AND-INTEGRATIONS.md): identity/privacy, offline/hardware, simulator, future HubSpot and MoZeus departure.
7. [06 — Setup and recovery](06-SETUP-AND-RECOVERY.md): owner-handled access, safe inspection, worktree inventory, setup/testing and deployment precautions.
8. [07 — Decisions and open questions](07-DECISIONS-AND-OPEN-QUESTIONS.md): implemented rules, agreed direction, future decisions and evidence gaps.
9. [08 — New Codex starter prompt](08-NEW-CODEX-STARTER-PROMPT.md): paste-ready read-only first-session instructions.

## Verification and provenance

Snapshot: branch build-9f-event-reporting, HEAD 01e48c2bc0adcac0a5047291791cdda40cbe5328. Source/model/route/config-name inspection, Git ancestry/worktree/status review, relevant build docs and available historical chat messages were examined. Fresh tests used isolated in-memory SQLite and config.settings.test: **678 run, OK (skipped=1), 38.207 seconds**. Django check and migration drift check passed. Fresh preview /health/ returned status ok. Prior 9F delivery evidence includes 72 focused tests, populated migration preservation, desktop/mobile acceptance, all four CSV downloads and exact Render Live commit. See 04 for distinctions and unrun checks.

Actual paths cited in the documents are repository-relative unless explicitly prefixed /workspaces/. docs/ and DOCS/ are separate on Linux. This package lives under docs/migration-handoff in the 9F worktree; no existing app/configuration/dependency/schema file is intentionally changed. No commit, push, merge, deployment, ownership transfer or provider action is authorized by creating it.

Historical chats consulted where readable: “Confirm Staff Deadline Inclusion,” “Code Conversation,” “HubSpot Integration Architecture,” and “UI Conversation about new app.” Some responses/attachments were inaccessible content references, so their details are not reconstructed. Older requirements may be superseded by actual later implementation or current owner instructions. Latest owner steering: moving away from MoZeus, Build 10 to be worked on later, owner handles new-account access.

## Preservation / transfer warning

The repository has multiple unrelated dirty worktrees and a separate 6B XML branch outside 9F ancestry. Before handoff creation, 9F had one untracked terminal artifact; it remains unchanged. These files are not automatically transferred by cloning. This package contains no private runtime files, database records or secrets, and is not an environment/data backup. Until approved and committed, it also will not appear in a fresh clone: provide the reviewed folder/archive directly to the next session.
