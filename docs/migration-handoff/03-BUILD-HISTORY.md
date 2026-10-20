# Available build history

Evidence hierarchy: Git ancestry/source at 9F is implementation evidence; build documents describe scope and recorded verification; current-session acceptance supplies browser/deployment evidence. Discussion is requirements evidence, not proof of implementation. Dates below are Git commit dates (UTC where applicable), not a reconstructed daily activity log.

## Verified ancestry leading to 9F

| Build / checkpoint | Commit | Objective / delivered change | Evidence and limits |
| --- | --- | --- | --- |
| Initial foundation | d1923eb; f71a45b | Initial project and hardened Django foundation | DOCS/ARCHITECTURE.md; UUID User, environment settings, permissions, explicit audit |
| Participant/account foundation | aec81cf | Canonical identity and account workflows | apps/participants/; no claim of a complete participant portal |
| Internal UI foundation | 149246c; f878660 | Staff shell/static assets | Historical foundation; later portal work supersedes layout |
| Events/ticket/legacy attendance foundation | 836f183 | Event registration, QR and attendance schema | Legacy attendance workflow later removed, data retained |
| Stations / experience workflow | b410eaa | Event/station staged experiences | apps/stations/ |
| Build 4 | 82337c4 | Kiosk quiz experience | apps/assessments/; docs/KIOSK_DESIGN_STANDARD.md |
| Build 5A / 5B / final | 0a0543e; 68dcdb5; 279fe79 | Game framework, playable Duck Hunt, finalized kiosk game experience | apps/games/; static/games/duck_hunt/; DOCS/GAME_FRAMEWORK.md |
| Build 6A | 61b3169 | Simulator server integration foundation | apps/simulators/; ingestion and review, not a physical Windows helper |
| Build 7A | 8c0b62c | Participant foundation | docs/PARTICIPANT_FOUNDATION.md |
| Build 7B | bbb2168 | Quiz administration, publishing, pools, test tools | docs/QUIZ_ADMIN_AND_TESTING.md |
| Build 7C-1 | 8a143c8 | Registration, smart intake and form builder | docs/PARTICIPANT_REGISTRATION.md |
| Build 7C-2 | e3840a9 | Registration/consent/ticket completion | docs/BUILD_7C2.md; original ticket foundations retained |
| Build 7C-3 / 7C-4A | 169dd57; 73e227a | Event questions/groups and offline local registration/tickets | DOCS/BUILD_7C3_7C4_STATUS.md |
| Build 7C-4B | 28855b2; a7c6673 | Sync and virtual central/trailer integration | DOCS/BUILD_7C4B_SYNC.md; BUILD_7C4B_VIRTUAL_INTEGRATION.md |
| Build 7C-4C | 8140274; 178aab6; 1aa0420 | Encrypted backup, portable recovery/reconciliation, communications/alerts | DOCS/BUILD_7C4C_OPERATIONAL_FOUNDATION.md; BUILD_7C4C_COMMUNICATIONS.md; earlier intermediate “not complete” notes may be superseded |
| Build 7E | d50c4a6 | Public event pre-registration | DOCS/BUILD_7E_PUBLIC_REGISTRATION.md |
| Build 7F | 5eb0078 | Central-to-trailer event preparation/downsync | DOCS/BUILD_7F_CENTRAL_TO_TRAILER.md |
| Build 7D | e771006 | Historical participant import/export | DOCS/BUILD_7D_HISTORICAL_DATA.md; commit order differs from letter order |
| Build 7 closure | 2e0339f | Remove legacy participant check-in workflow, finalize | DOCS/BUILD_7_COMPLETION.md; retain schema/history, no new arrival scanning |
| Build 8A | 3f14edd | Qualifications/provenance/catalog foundations | DOCS/BUILD_8A_QUALIFICATIONS_FOUNDATION.md |
| Build 8B | 38a54ed | Private participant document management | DOCS/BUILD_8B_DOCUMENT_MANAGEMENT.md |
| Build 8C | 04bee00 | Verification and approvals | DOCS/BUILD_8C_VERIFICATION_APPROVALS.md |
| Build 8D closure | 4100b72 | Qualification/document/approval integration | DOCS/BUILD_8_FINAL_CLOSURE.md: recorded 277 passed + 1 skipped; 27 focused; virtual regression scripts passed. Defined software scope, not production-ready |
| Build 8E audit | fd996d2 | Read-only portal audit and planning | Historical gaps are not automatically current bugs |
| Build 8E portal refinements | e17d696; b3b6b53; f8757e5; 5edd0a9; cdaf3f8; 3a52818 | Portal foundation, event authorization, workspaces, participant directory, six-section navigation, local Futura, Settings fixes | DOCS/BUILD_8E_*; preserve shell rather than rebuild |
| Home phases | 3843c88; dcb1013 | Dashboard appearance and per-user Quick Actions | DOCS/HOME_DASHBOARD_PHASE2A.md; Updates/Analytics remain visual previews |
| Build 8E-C | 15cf2a6 | Participant evidence/review portal integration | DOCS/BUILD_8E_C_QUALIFICATIONS_DOCUMENTS_APPROVALS.md |
| Build 8E-D | 81d2a2f | Event/offline integration safeguards | DOCS/BUILD_8E_D_EVENT_OFFLINE_INTEGRATION.md |
| Build 9B | 63838615fdde39e479a16752c2daa7317f4b6d94 | Event management/lifecycle/logistics UI | 30 focused; 409 full run with one skip; populated migration preservation and desktop/mobile evidence in build doc |
| Build 9C | 98c2a4ac2d65929535f9b9dae0a0586b4836a541 | Staff profiles, availability, daily coverage, confirmed assignment revisions | 68 focused; 477 full run with one skip; baseline 9B; DOCS/BUILD_9C_STAFFING.md |
| Build 9D | bb5d30e0ffc7e847c7275a2656a9468094d11108 | Tasks, event notes, operational document boundary | DOCS/BUILD_9D_OPERATIONS.md; real uploads disabled pending durable storage; 9E full acceptance ran 606 after adding its tests |
| Build 9E | cb83021ec5176b08660054ee03fea2729308abfa | Staff daily check-in/workday compensation and individual mileage | 65 focused; 606 full run with one skip; four migrations; preview Live and browser evidence in delivery report |
| Build 9F | 01e48c2bc0adcac0a5047291791cdda40cbe5328 | Expenses, incentives, approved staff costs, event/cross-event metrics and CSVs | 72 focused; 678 full run with one skip; three migrations; deployed Live and browser accepted |

Build 9F changed 33 files (1,272 insertions, 10 deletions). The new app, scoped templates/CSS, navigation links, app/routes registration, one 9B test expectation and DOCS/BUILD_9F_EVENT_REPORTING.md are in that commit. No 9G or Build 10 implementation was performed in this session.

## Separate Build 6B line — do not silently fold into 9F

Cached origin/build-6b-simulator-results points to **2b4ae2b705825d6dbe16a3b4728387d163a48672** (“Build 6B: finalize simulator XML collection and results monitor”, October 9). It is **not in 9F ancestry**. Its docs/SIMULATOR_WINDOWS_HELPER.md and simulator_helper/XML_COLLECTION.md describe a Windows Tkinter XML monitor, local original-byte archive and SQLite index. Normal monitoring is XML; OCR is legacy diagnostics. Scores are Pending Formula. No XML delivery to Django is included in that checkpoint.

The old /workspaces/built-to-work worktree additionally has uncommitted OCR helper/calibration-import work and a ZIP. These files are separate from the committed XML branch and must be preserved; do not assume either is the authoritative deployed sender. No helper tests or Windows hardware checks were rerun for this handoff.

## Historical evidence gaps

- Build 1–3 numeric labels and a standalone Build 9A implementation cannot be fully reconstructed from the inspected documents; the listed commits establish actual changes without inventing labels. 9B/9C documents reference earlier discovery; no standalone 9A roadmap file was found.
- Several older ChatGPT responses resolve only to content-reference placeholders. In particular, the exact earlier Build 10 response and one attachment body could not be recovered. User-authored messages about Home layout and later check-in were readable.
- This package does not replicate all old conversations, issues, attachments, terminal buffers, real import files, or every local branch artifact. Source and Git history are the durable implementation record.
- Earlier Build 8/9 reports are historical acceptance evidence, not tests freshly rerun at those historical SHAs. Current 9F was freshly tested; see 04.
- Build 9F preservation evidence after a Codespace restart uses a post-reconnect checkpoint; the earlier temporary snapshot had disappeared. Do not claim an uninterrupted original pre-task hash trail.
- Cached remote refs were inspected without fetching; verify server refs when the new account is connected. Do not reset the older local render-preview checkout to match them.

## Established working process

The owner provides a scoped numbered build brief with objective, baseline, discovery requirements, feature rules, UI scope, reuse boundaries, exclusions, tests, deployment instructions and final report. Inspect source, previous documents, current Git state and authenticated Base44 read-only where available before implementation. Resolve business rules through evidence or owner clarification; never fabricate rates, participant activity, policy or completion.

For approved development, use an isolated branch/worktree from the verified current preview baseline; preserve unrelated branches, dirty work and data. Build working backend and portal together, reuse domain services/components and maintain granular authorization, audit, history and migrations. Use clearly synthetic isolated fixtures. Correct discovered defects within scope, add meaningful regression coverage, rerun affected checks, and state remaining limitations honestly.

Acceptance normally includes focused tests, full suite using the project runner, Django checks, migration drift check, diff checks, populated additive-migration preservation where applicable, desktop/mobile review, manager/staff/admin permissions and actual exports. SQLite success is not PostgreSQL locking certification; virtual integration is not physical trailer validation.

Commit/push/deploy authority is **specific to the current request**, not inherited forever from a prior build. Prior build briefs sometimes authorized preview publishing after acceptance; the present handoff request expressly forbids commit/push/merge/deploy without approval. When authorized later, verify preview ancestry/newer changes, push normally, wait for Render Live, inspect migrations, health and deployed workflows, and report exact SHA. Never infer permission for main, production, infrastructure changes, force-push, new dependencies, broad refactors or next builds. Stop after the approved scope and review report.
