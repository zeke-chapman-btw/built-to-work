# BTW historical roadmap: recovered record after Build 9F

Prepared October 20, 2026 (America/New_York). **Historical documentation, not implementation authorization.**

## Finding and interpretation

Substantial build numbering and objectives survive in Git and original user-supplied build-request attachments. A single complete, authoritative original 20-build master document, its version/date, and its approval record were **not recovered**. This record preserves the surviving evidence without claiming that references from different planning periods form one definitive original version.

Builds 0–20 below mean 21 numbered entries including the preliminary Build 0. The owner's phrase “20-build roadmap” is retained; this counting convention does not establish Build 20's missing scope.

Evidence classes:

- **Historical requirement:** an original preserved request or planning reference; not proof of implementation.
- **Implemented checkpoint:** source/Git/build evidence at the completed 9F baseline, with explicit exceptions for separate 6B work.
- **Recorded acceptance:** tests or browser/deployment checks documented earlier; not rerun for these drafts.
- **Unrecovered:** insufficient evidence for a title, detailed objective, completion or cancellation. Do not fill the gap by inference.
- **Proposal:** only in the separate reconciliation document; requires owner review.

Repository: https://github.com/zeke-chapman-btw/built-to-work

Application baseline: **01e48c2bc0adcac0a5047291791cdda40cbe5328**, branch **build-9f-event-reporting**, existing worktree **/workspaces/btw-build-9f**. Migration-handoff evidence is pinned to **6c3ced07f85779e4f6bd1234d4cde3ec1ede65b2** on **docs/migration-handoff-9f**. Sources R1–R5 and H1–H12 are defined below.

## Recovered Builds 0–20

Names below use surviving request/build wording where available. A combined scope label is used where no single original title survives. Objectives are concise paraphrases, not reconstructed full acceptance criteria.

| Build | Recovered historical name / objective | Implementation status after 9F | Evidence |
| --- | --- | --- | --- |
| **0** | **Platform Foundation**: establish the Django architecture, settings, custom user, permissions and explicit auditing foundation. | Completed foundation; initial d1923eb, hardened f71a45b. No reason to repeat it during account migration. | H1/H2 identify completed Build 0; R1 maps commits and source. |
| **1** | **Participant + Participant Account Foundation**: permanent Participant identity and optional authenticated Participant Account. | Completed aec81cf; account foundation is not the full later participant portal. | H1; R1/R2; apps/participants/. |
| **2** | **Events + Event Registration + QR Tickets + Check-In / Attendance**: bind participants to events and issue/recover tickets with the then-planned attendance flow. | Completed 836f183. Participant arrival/check-in UI subsequently removed in Build 7; legacy history retained. | H2; R1/R2. |
| **3** | **Stations + Event Experience State Machine**: connect registration/event context to station sessions and enforce activity progression. | Completed b410eaa; existing progression reused by assessments, games and simulator acceptance. | H3; R1/R2; apps/stations/. |
| **4** | **Quiz / Knowledge Assessment**: participant kiosk assessment with timed categories, saved results and recovery. | Completed 82337c4 and later UI acceptance; physical touchscreen/scanner certification is separate. | R1/R2; docs/KIOSK_DESIGN_STANDARD.md; apps/assessments/. |
| **5** | Game framework and **Duck Hunt kiosk experience**: configurable gameplay, scoring, saved results and progression. | 5A 0a0543e, 5B 68dcdb5, final Build 5 279fe79. Actual phase labels differ from an earlier proposal. Physical lightgun acceptance remains separate. | R1/R2; H11; DOCS/GAME_FRAMEWORK.md. |
| **6** | Simulator integration: receiving, evidence association, review and accepted results; later physical Windows collection. | 6A 61b3169 implemented in 9F ancestry. Separate 6B XML monitor 2b4ae2b exists outside that ancestry; local collection only, Pending Formula, no XML delivery to Django. End-to-end hardware integration not complete. | R1/R2; docs/SIMULATOR_HELPER_CONTRACT.md; separate branch docs. |
| **7** | Participant profile/foundation and registration-related administration, expanded into forms, consent, tickets, historical import/export, public registration and offline operations. Exact single original umbrella title not recovered. | Software scope completed through 2e0339f. Includes 7A, 7B, 7C subdivisions, 7D, 7E and 7F; not blanket field/production readiness. | R1/R2; R5 describes synchronization boundaries; H8 captures older allocations. |
| **8** | Participant documents and readiness: standardized qualifications, supporting documents, verification/approvals; later portal integration. | 8A 3f14edd; 8B 38a54ed; 8C 04bee00; 8D closure 4100b72. Additional 8E portal/integration work delivered through 81d2a2f. | H5/H6; R1/R2; DOCS/BUILD_8_FINAL_CLOSURE.md. |
| **9** | **Event Operations & Finalization**: operational event workflows informed by the existing Base44 application. | 9A discovery request recovered; no separate implementation commit established. 9B–9F implemented. 9F complete for approved development/preview scope, not proof that every original umbrella requirement is complete. 9G unresolved. | H4/H9/H10; R1/R3/R4. |
| **10** | **Stations, Quizzes & Scoring**: later station/quiz/scoring work; 8E-D exclusions also mention quiz authoring, question-bank and station UI redesign. Full original brief not recovered. | No completed Build 10 checkpoint found. Earlier stations/quizzes/scoring foundations already exist. Latest planning deferred; Home/UI discussions must not silently replace this historical allocation. | H4/H7; conflicting H8; R3. |
| **11** | **Participant Portal**: self-service participant profile/qualifications and document upload/management. | Planned full portal not implemented. Existing participant account/login foundation and internal staff profile do not establish portal completion. | H4/H5/H6; R2/R3/R5. |
| **12** | **Central Data & Synchronization**: broader central/trailer data integration; qualification/document/verification protocol requirements deferred here. | Planned broader scope remains incomplete. Build 7 already supplies scoped registration synchronization/downsync and recovery. An 8E-D request instead labels Build 12 customer portal; conflict preserved. | H4/H5/H7; R2/R5. |
| **13** | Within **Builds 13–15: Customer and Hiring Workflows**. | **Individual title and objectives unrecovered.** No customer/employer/campaign implementation found at 9F. Do not assign Build 13 a guessed title. | H4; R2. |
| **14** | **Candidate Matching**: qualification/profile foundations intended to support later matching. Algorithm, validity and acceptance rules unrecovered. | Planned; candidate matching workflow not found. Existing assessments and qualification records are prerequisites, not matching itself. | H5/H6; R2/R5. |
| **15** | **Customer Portal / Controlled Customer Access**: authorized employer/customer access to permitted participant information. | Planned; no employer portal/access-grant workflow found. Customers navigation is a labeled placeholder. | H4/H5/H6; R2/R5. |
| **16** | **HubSpot Integration / CRM Synchronization**: later aligned participant/intake information and communication integration. | Planned, not implemented. No deployed field map, ownership/conflict contract or CRM synchronization exists in 9F. | H5/H6; R2/R5. |
| **17** | **Staff Management & Payroll**: later staffing/workday/payroll scope. Complete original acceptance criteria unrecovered. | Staffing subset delivered in 9C; workday, pay/per diem and true mileage ledger in 9E. Full original Build 17 is **not certified complete**; payroll payment/tax/provider integration not delivered by 9F. | H4/H9; R1–R4. |
| **18** | **Expenses, Tasks & SOPs**: later operational management scope. Complete original acceptance criteria unrecovered. | Task/note/document boundary in 9D; expenses/incentives in 9F. No evidence that full original SOP system/acceptance is complete. Real operational uploads remain disabled. | H4/H9; R1–R4. |
| **19** | **Billing, Reporting & Analytics**: later financial/commercial reporting scope. Complete original acceptance criteria unrecovered. | Event cost/performance reporting and four CSV exports delivered in 9F. Billing/accounting/customer invoicing are not established as implemented. Full original Build 19 is not complete by inference. | H4; R2/R4. |
| **20** | **Name and objectives unrecovered.** | No verified implementation, cancellation, merger or replacement found. Absence of evidence is not an approved deletion. | Search coverage and gaps below. |

## Delivered subdivisions and checkpoint evidence

| Checkpoint | Delivered objective / boundary | Commit |
| --- | --- | --- |
| 7A / 7B | Participant foundation / quiz administration, publishing, pools and test tools | 8c0b62c / bbb2168 |
| 7C-1 / 7C-2 | Smart intake/form builder / registration, consent and tickets | 8a143c8 / e3840a9 |
| 7C-3 / 7C-4A | Event questions/groups / local offline registration and ticket interface | 169dd57 / 73e227a |
| 7C-4B | Synchronization and virtual central/trailer integration | 28855b2 / a7c6673 |
| 7C-4C | Encrypted backups, recovery/reconciliation, communications and operational alerts | 8140274 / 178aab6 / 1aa0420 |
| 7D / 7E / 7F | Historical import/export / public registration / central-to-trailer preparation | e771006 / d50c4a6 / 5eb0078 |
| 8E and Home | Staff portal/navigation, participant directory, personal Settings, per-user Quick Actions, evidence and event integration | R1 lists individual commits; Home dcb1013; final event integration 81d2a2f |
| 9A | Events & Operations Discovery and UI Planning; request expressly planning-only | H10; no separate implementation SHA recovered |
| 9B | Event management foundation and UI | 63838615fdde39e479a16752c2daa7317f4b6d94 |
| 9C | Staff profiles, availability and confirmed staffing revisions | 98c2a4ac2d65929535f9b9dae0a0586b4836a541 |
| 9D | Tasks, event notes and private operational-document boundary | bb5d30e0ffc7e847c7275a2656a9468094d11108 |
| 9E | Staff workdays, individual mileage and compensation configuration | cb83021ec5176b08660054ee03fea2729308abfa |
| 9F | Expenses/incentives, approved staff costs, source reconciliation, official performance metrics and CSVs | 01e48c2bc0adcac0a5047291791cdda40cbe5328 |

## Numbering conflicts and scope evolution: do not erase

1. **Build 9 synchronization versus event operations.** H8 allocates full trailer/cloud synchronization to Build 9. H4 instead names Build 9 Event Operations & Finalization and Build 12 Central Data & Synchronization. Substantial registration sync was implemented in Build 7. This establishes differing allocations, not an approved universal renumbering.
2. **Participant Portal: Build 10 versus 11.** H8 says Build 10; H4–H6 say Build 11. Both are historical evidence. Their original approval chronology and formal supersession were not recovered.
3. **Customer Portal: Build 12 versus 15.** H7 excludes “Build 12 customer portal”; H5/H6 assign customer portal/access to Build 15. Preserve the discrepancy rather than deciding which sentence was a typo.
4. **Original Build 10 versus current Home/UI conversation.** Stations, Quizzes & Scoring is a verified historical title. Later Home tasks, Quick Actions, Updates/Analytics and a Base44 audit appear in the handoff under deferred Build 10 discussion. Earlier briefs called a comprehensive UI pass Build 9G. No approved merger, rename, replacement or sequence has been established.
5. **Later operational features delivered in 9C–9F.** Historical 17–19 areas overlap with delivered staffing, compensation, tasks, expenses and reporting. Subset delivery is not retirement or completion of the original later build. No formal scope-closure crosswalk was recovered.
6. **Build 5 subphase divergence.** H11 proposed 5A hardware/input, 5B gameplay, 5C styling, 5D saved flow/results, 5E recovery/testing. Git instead records 5A configurable game framework, 5B playable Duck Hunt, and a final Build 5 checkpoint. Do not invent separately completed 5C–5E commits from the proposal.
7. **Attendance removal.** Build 2 included participant check-in/attendance. Build 7 closure removed the active arrival/check-out workflow while preserving legacy data. Staff workday check-in is a different domain. Do not resurrect participant check-in because an old roadmap mentions it.
8. **Simulator direction.** Earlier planning proposed screen/OCR collection and participant UUID handling. Separate 6B now describes XML collection with Pending Formula and no Django delivery. The 9F receiver and XML sender have unresolved identifier/score/sync boundaries; they are not one deployed pipeline.
9. **13-phase plan is a separate numbering scheme.** H12 describes approximate architecture phases, not Build 1–20: (1) architecture mapping/decision lock; (2) participant identity/account; (3) registration/QR/attendance; (4) authorization/data boundaries; (5) event lifecycle/trailer context; (6) station/quiz/activity/simulator sessions; (7) profile/forms/documents/consent; (8) customer grants/visibility; (9) campaigns/hiring needs; (10) candidate lifecycle/customer actions; (11) billing/accounting reporting; (12) trailer/cloud sync; (13) HubSpot/communications. Never equate its Phase 13 with missing Build 13.
10. **20-item backlog is not a 20-build roadmap.** H11 calls itself a product/workflow backlog and lists item 20 as Overall build roadmap — Not finalized. Its item 13 assessment categories and item 20 roadmap cannot supply the missing Build 13 or Build 20 objectives.
11. **Handoff evidence gaps are partially resolved.** R1 originally could not establish all early numeric labels or a standalone 9A roadmap file. H1–H3 now establish Build 0–3 labels; H10 establishes a 9A discovery request. This does not establish a separate 9A implementation or change the pinned handoff. These new local findings have not been added to GitHub.

## Application state and verification limits

Build 9F is complete for the approved development/preview scope. R3 records 72 focused tests, 678 full-suite tests run successfully with one skip, populated 9E-to-9F migration preservation, three deployed reporting migrations, desktop/mobile review and four inspected CSV exports. The handoff reran 678 tests with one skip, Django checks and migration drift checks. **These are recorded historical results; this documentation task ran no application tests or migrations and made no new hardware/production acceptance claim.**

The prior continuity check verified the 9F worktree at the expected SHA with no tracked/staged changes, ten original untracked handoff copies plus the retained terminal artifact; the documentation worktree was clean. All 15 original worktree fingerprints matched the pre-preservation baseline. Local render-preview remained older and dirty, while remote render-preview matched 9F. These checks were not rerun for the current writing task.

Known unresolved application boundaries at the 9F checkpoint: real operational uploads disabled pending durable private storage; actual mileage rate/effective date unconfigured; per-staff compensation role restrictions absent; Home task panel absent and Updates/Analytics still previews; some score-display precision debt; local-date versus UTC filter semantics; no broad 8–9F offline replication; no complete participant/customer/recruiting portals, billing or HubSpot integration; no PostgreSQL concurrency, physical hardware or real-provider certification. Detailed mapping and dependencies are in the separate proposal.

## Supporting evidence: immutable repository references

| ID | Source | What it establishes |
| --- | --- | --- |
| R1 | [Migration build history](https://github.com/zeke-chapman-btw/built-to-work/blob/6c3ced07f85779e4f6bd1234d4cde3ec1ede65b2/docs/migration-handoff/03-BUILD-HISTORY.md) | Commit lineage, completed build subdivisions, separate 6B, working process and original evidence gaps. |
| R2 | [Feature inventory](https://github.com/zeke-chapman-btw/built-to-work/blob/6c3ced07f85779e4f6bd1234d4cde3ec1ede65b2/docs/migration-handoff/02-FEATURE-INVENTORY.md) | Implemented, partial, planned and not-found boundaries with source paths. |
| R3 | [9F status and deferred Build 10](https://github.com/zeke-chapman-btw/built-to-work/blob/6c3ced07f85779e4f6bd1234d4cde3ec1ede65b2/docs/migration-handoff/04-CURRENT-STATUS-AND-BUILD-10.md) | Recorded acceptance, limitations, Home/UI discussion and unresolved scheduling. |
| R4 | [Build 9F implementation record](https://github.com/zeke-chapman-btw/built-to-work/blob/01e48c2bc0adcac0a5047291791cdda40cbe5328/DOCS/BUILD_9F_EVENT_REPORTING.md) | Ledger/source cost rules, reconciliation, official metrics, permission boundaries, exports and exclusions. |
| R5 | [Data and integrations](https://github.com/zeke-chapman-btw/built-to-work/blob/6c3ced07f85779e4f6bd1234d4cde3ec1ede65b2/docs/migration-handoff/05-DATA-AND-INTEGRATIONS.md) | Canonical data ownership, privacy, limited synchronization, XML/receiver boundary and future HubSpot direction. |

R1–R4 were fetched again from their exact commits during preparation. R5 and repository source had been inspected during the preceding recovery/continuity work. Implementation references are pinned to 9F rather than inferred from discussion. Latest owner instructions supersede older suggested scheduling but do not rewrite historical evidence.

## Supporting evidence: original local build requests

These are user-supplied historical attachments, not newly approved instructions. They are **not in GitHub simply because this record cites them**. Full attachment files were not copied, to avoid bundling unrelated context. The recoverable scope excerpts below make the principal findings portable; hashes identify original byte content but do not establish authorship, timestamp or approval.

Each original path is `C:\Users\zchapman\.codex\attachments\<attachment ID>\Pasted text.txt`. That machine-local location may be unavailable to another account/computer.

| ID | Attachment ID | Relevant location and recovered evidence |
| --- | --- | --- |
| H1 | 01576dc7-df20-42c8-8439-974f94c00786 | Opening lines: Build 0 Platform Foundation complete; Build 1 Participant + Participant Account Foundation; permanent identity and optional account purpose. |
| H2 | 0d83b352-a3aa-49fe-9d96-3b8f688ce0fa | Opening: Build 2 Events + Event Registration + QR Tickets + Check-In / Attendance; completed 0/1 baseline. |
| H3 | 3ade7cf7-92c8-41ed-957f-b11da1692bb8 | Opening and Build 3 Goal: Stations + Event Experience State Machine; completed 0–2 and frontend baseline. |
| H4 | a94a9d0d-c051-4fc4-9c22-b010601fdd11 | Section 9, lines 176–189: Build 9 Event Operations & Finalization; 10 Stations, Quizzes & Scoring; 11 Participant Portal; 12 Central Data & Synchronization; 13–15 Customer and Hiring Workflows; 17 Staff Management & Payroll; 18 Expenses, Tasks & SOPs; 19 Billing, Reporting & Analytics. |
| H5 | 452a883e-14d5-4121-a9e2-c403cc7ba152 | Objective, lines 17–32: 8A qualification foundation supports 8B Supporting Documents, 8C Verification & Approvals, 11 Participant Portal, 14 Candidate Matching, 15 Customer Portal, 16 HubSpot Integration. |
| H6 | cb91468d-984c-4ac3-a365-b8b089f88fb3 | Later-build compatibility, lines 229–232: 11 participant uploads/profile editing; 14 candidate matching; 15 controlled customer access; 16 appropriate CRM synchronization. |
| H7 | c0848f16-7630-49ed-9c0d-1b7a9de36819 | 8E-D Build boundaries, lines 286–288: 10 quiz authoring/question-bank/station UI redesign; 11 participant portal/profile redesign; **12 customer portal**. |
| H8 | e8ac40d6-3f1f-4d87-96a0-d31ef5dd4ecc | 7C-2 Scope boundaries, lines 581–596: **9 full cloud/trailer synchronization**, **10 Participant Portal**; other later scopes excluded. |
| H9 | c5319cf7-1e1e-4b63-b1d3-2930069bcd21 | Lines 320–321: daily workday/payroll deferred to 17; expenses/tasks/SOPs deferred to 18. Later 9E/9F implementation establishes subsequent scope expansion. |
| H10 | c9dfc4cb-a3f0-40e7-bb00-e8e3d645b6bb | Opening: Build 9A Events & Operations Discovery and UI Planning; explicitly discovery/planning only, no implementation or deployment. |
| H11 | 7ba2c249-c33b-4393-aa88-67946f208d42 | Opening backlog table: 20 product/workflow topics, item 20 roadmap not finalized. Proposed Build 5 phases later in file; file cuts off during registration certifications. |
| H12 | f94a3bca-8fc3-4904-adc8-60dffd4c1aa8 | Update Implementation Roadmap, approximately lines 643–703: distinct approximate 13-phase architecture plan. |

### Preserved exact scope excerpts

From H4, the historical later-build allocation:

> Build 9 — Event Operations & Finalization  
> Build 10 — Stations, Quizzes & Scoring  
> Build 11 — Participant Portal  
> Build 12 — Central Data & Synchronization  
> Builds 13–15 — Customer and Hiring Workflows  
> Build 17 — Staff Management & Payroll  
> Build 18 — Expenses, Tasks & SOPs  
> Build 19 — Billing, Reporting & Analytics

From H5, additional numbered dependencies:

> Build 14 — Candidate Matching  
> Build 15 — Customer Portal  
> Build 16 — HubSpot Integration

Conflicting preserved exclusions:

> H7: Build 12 customer portal.

> H8: Full cloud/trailer synchronization — Build 9  
> Participant Portal — Build 10

H11's own scope statement and item 20:

> This conversation covers a 20-item backlog of product and workflow planning while a separate coding chat handles larger builds.

> Overall build roadmap | Not finalized

### Original attachment SHA-256 manifest

| Evidence | SHA-256 |
| --- | --- |
| H1 | 2CAEE2F1F73DD99089A061DABEBC4A8F9B4032B71EBF75046069C5AB2D73E8C3 |
| H2 | 0D235F94EF29512F52C7138640B6F66704A6A626FBE0A80252232A4C96054FE6 |
| H3 | 97518B82D4DD2398CD09C019A49A7252AFC4C948D7AF8E42ECCDAEA00EC19BC2 |
| H4 | A3C5071C6623B1144B706DC1B11011CA9341069638E7F7EEC024E59BE5622587 |
| H5 | 3DE41041E0D2496A7F1DE226669CF3B4248472C13A4C25200F8FD65EE861343D |
| H6 | 37719C0FCAE66DDCB81BD024EA348ECF9CD488897217D08D948D0D38D0CA4CDF |
| H7 | 96BEABF6FFF800DD46F415065D01FCA2AFF45BA42EB884F63021583F9FE2267D |
| H8 | 2E26930DF3BFA624B9AC16D32F6D5C6F94DF04C6C22A3AB36227C0098EFABCE5 |
| H9 | 70D914B05A4D1BB60C7486E4BC9102E768E43308D4C1F3AC18F0E9E3241A83E9 |
| H10 | A9B7D7D6640E6BDFCBDC9DF193FEF8C9A4259A97EA2D74EB396582EBFD639140 |
| H11 | 2F3464F9E7A77CF434198F90C364A16005BB239C9FDDDF5BD960154B02838F62 |
| H12 | D7367869FFA5A066E99500665224B89581E2A0F52A45CF6F814BDCFBFAAE85BB |

## Search coverage and remaining gaps

The preceding read-only recovery searched documentation at existing local/remote-tracking branch tips and all reachable historical Markdown/text/reStructuredText blobs (63 distinct document versions), available commit history, the preserved migration handoff, local build-request attachments and accessible project conversations. GitHub default-branch code search for roadmap and commit searches for roadmap/Build 10/Build 20 returned no master-plan evidence. Negative search results are limited to those accessible sources; they do not prove an original plan never existed in another account, deleted Codespace, unreachable Git object, private document or inaccessible conversation.

The conversation titled “UI Conversation about new app” corroborates the 20-item backlog; newer “Build Prompts” and “Built to Work — Master Business Context” did not expose a complete original build plan. Some historical assistant responses are only content-reference placeholders. Conversation summaries and links are not substitutes for missing source content.

Still missing: authoritative complete 20-build source/version/date/approval; individual Build 13 title/objectives; all Build 20 details; most later acceptance criteria and dependency contracts; formal renumbering/supersession decisions; full original 17–19 scope closure; exact relationship among 9G, Home work, Base44 audit and Build 10. Do not infer dates or approval order from attachment UUIDs or current file timestamps.

No original source has been overwritten. This record corrects the scope of what is recoverable, without approving new scheduling or changing existing application behavior. See [the separate reconciliation proposal](02-PROPOSED-RECONCILIATION.md) for owner review questions.
