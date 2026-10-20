# Decisions and open questions

## Confirmed continuity/process decisions

| Decision | Rationale / evidence |
| --- | --- |
| Same application/repository/history/architecture/workflow | Explicit owner instruction: workspace transition only; no greenfield rebuild/tool or hosting change |
| Documentation only; no commit/push/merge/deploy now | Explicit handoff boundary; new code requires owner approval |
| Build 9F is the checkpoint | Verified SHA/source/tests and earlier deployed acceptance |
| Build 10 will be scoped later | Latest owner reply during handoff; do not block documents on exact earlier brief |
| Owner handles new-account access | Latest owner reply; retain access checklist for their use without initiating transfers |
| Move away from MoZeus | Latest owner reply; no further dependency reconstruction or connector work in this task |
| Preserve unrelated dirty work and separate branches | Explicit owner requirement and actual worktree discovery |

## Implemented architectural/product decisions

| Decision | Rationale / source |
| --- | --- |
| Django custom UUID User + built-in Groups/Permissions | Stable identity and existing authorization model; DOCS/ARCHITECTURE.md |
| Explicit audit calls, not save signals | Caller knows actor/action/reason; controlled minimization. Audit is not tamper-proof evidence |
| Reuse canonical Participant, Event and source services | Avoid duplicate identities/results/compensation/notes |
| Registration/activity scan is not participant arrival attendance | Build 7 removed check-in workflows; preserve legacy data rather than resurrecting UI |
| StaffProfile may be roster-only; explicit User linking | Scheduling does not create accounts or grant permissions |
| Request approval immediately confirms assignment | No second acceptance; revisions preserve prior dates/decisions/history |
| Daily pay role is independent of driving declaration | Role responsibilities and individual mileage are separate concepts |
| True per-mile reimbursement replaces Base44 fixed tiers | Explicit owner direction; actual rate/effective date remains missing, not invented |
| Historical approved financial snapshots are stable | Configuration changes do not rewrite approved pay/per diem/mileage |
| Distributed incentives counted once; purchases excluded | Prevent purchase/distribution duplication; manual staff references do not duplicate source compensation |
| Unknown costs require reconciliation, not assumed zero | Preliminary subtotal differs from complete actual cost; stale sources invalidate review |
| Official accepted evidence only for metrics | Repeated scans, test/demo/unresolved/void results do not become official completions |
| Shared six-section portal and Settings gear retained | Later UI planning should reuse current shell and identity |
| Real operational uploads gated by verified durable private storage | Avoid loss through temporary hosted filesystem; synthetic demos labeled clearly |
| 6B XML monitoring is separate from deployed 9F | Branch docs supersede normal OCR path there; no score formula/server sync implied |

## Agreed direction / requirements not yet implementation

- Future HubSpot is intended for post-event communication/intake with aligned canonical fields and synchronization back to BTW; BTW retains master participant history and customer access control. Stable-ID mapping, provenance/conflict policies and detailed sync contract remain design work.
- Future employer/customer records, campaigns/positions, candidate bundles and controlled anonymized/full sharing are product ambitions, not current portal features.
- The owner wanted per-employee role eligibility and listed Staff, Staff Lead, Driver, Set-Up Only and Combined. Current check-in offers globally active roles; no per-profile allowlist was found. Do not represent this requirement as delivered or infer a Set-Up Only rate.
- General/Home-based staff check-in was discussed for later; current event/assigned-day workflow remains unchanged.
- Home task visibility, compact Quick Actions, Updates/Analytics decisions and a Base44 visual/workflow audit are future planning topics. Four quadrants were proposed as a question, not approved as a fixed layout.
- Old documents mention Build 11 participant portal and Build 12 privacy-reviewed later synchronization. Retain these as historical roadmap references, not newly approved schedules or fixed numbering.

## Open questions to resolve when development resumes

1. What is the next approved brief? Confirm Build 9G vs Build 10 naming/relationship and precise scope later, as the owner requested.
2. Which existing task controls and role-scoped summaries belong on Home? What takes priority on desktop/tablet/mobile? Should analytics remain small, move, or expand? What does Updates contain and what does read/unread mean?
3. What are the actual approved mileage rate and effective date? Are per-employee allowed roles and Set-Up Only configuration now needed? Do not change compensation without policy confirmation.
4. How should the separate 6B XML monitor eventually connect to Django? Resolve ticket-vs-phone identifier semantics, formula version, event/session matching, idempotent delivery, authentication, privacy, reconciliation and nonofficial testing first. Do not assume XML collection equals an official score.
5. Which local simulator/UI changes are worth preserving/merging/archiving? Review with owner; no automatic cleanup. Is there a separate physical-device repository/state that must also be retained?
6. What durable private operational storage is approved, and which production access/backup/retention/file-inspection checks are required before activation?
7. What are the finalized HubSpot field map, ownership/conflict rules, communication consent, retry/deletion semantics and customer visibility policy? Nothing here authorizes actual sync.
8. Which post-7 domains must work offline, and what versioned protocol/privacy scope is approved? Existing sync is not a generic database replica.
9. When should PostgreSQL concurrency, physical trailer devices/network/printing, real provider delivery and disaster recovery be certified? SQLite/virtual checks do not replace them.

These are future decisions, not blockers to documentation review. Account access is owner-handled; MoZeus reconstruction and Build 10 planning are explicitly deferred.

## Risks and assumptions to carry forward

- An older dirty Explorer checkout can cause edits against the wrong branch. Verify directory, branch, HEAD and status every time.
- Pushing render-preview triggers deployment. A documentation commit is not automatically harmless to publish.
- GitHub does not contain uncommitted/ignored local state. Moving accounts without preserving the existing Codespace can lose separate work.
- Historical audit/roadmap documents can be stale. Prefer actual source/current tests and explicitly distinguish requirements from implementation.
- Dependency ranges and Python version differences can change a new environment; do not silently update established dependencies.
- Identity keys, ten-digit ticket strings, historical snapshots and provenance must survive future changes; no naive email/phone merge or score-field overwrite.
- Candidate/employee/customer data and environment secrets must remain outside the handoff. Read-only discovery does not authorize export or transmission of private data.

## Evidence gaps and interpretation rules

Some historical ChatGPT answers/attachments were inaccessible via the thread tool; references alone were not expanded into invented decisions. Recovered user messages and the present owner's instructions are labeled separately from assistant proposals. “Implemented and verified” in the feature inventory means tested within the stated software/session boundary, never an assertion of field/production readiness. Cached remote refs were inspected without fetching. A later session should verify drift before proceeding.
