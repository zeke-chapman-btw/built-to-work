# BTW proposed roadmap reconciliation — owner review required

Prepared October 20, 2026 (America/New_York). **PROPOSAL ONLY. Not an approved roadmap, build brief, replacement numbering scheme, development authorization or deployment instruction.**

Read [the historical record](01-HISTORICAL-ROADMAP.md) first. Source IDs below refer to that document. No missing Build 13 or Build 20 details are supplied here.

## Baseline and proposed reconciliation method

Use the completed Build 9F application at **01e48c2bc0adcac0a5047291791cdda40cbe5328**, existing **build-9f-event-reporting** branch and **/workspaces/btw-build-9f** worktree as the comparison baseline. Keep **docs/migration-handoff-9f** and **build-6b-simulator-results** separate. This draft does not change branch structure, assign new build numbers, merge work, or select a new development environment.

Proposed review method: retain historical build names as evidence, map delivered capabilities to them, and ask the owner which remaining requirements still matter. Approve a concrete next brief only after reviewing those differences. Do not repeat completed work just to make the historical numbering look sequential, or mark a whole build complete because a subset shipped early. These review labels are local to this draft, not a new development methodology.

## Historical allocation versus actual application after 9F

| Historical area | Already delivered earlier or elsewhere | Still incomplete / not established | Review implication |
| --- | --- | --- | --- |
| 10 Stations, Quizzes & Scoring | Stations/progression in 3; kiosk quiz in 4; game/results in 5; simulator receiver in 6A; quiz administration/pools/testing in 7B; integration safeguards in 8E-D. | Full original Build 10 brief absent; no evidence that its later authoring/redesign/scoring objectives are all satisfied. Physical hardware not certified. | Inspect actual gaps before approving a later station/quiz build. Preserve accepted scoring/progression rather than duplicate engines. |
| 11 Participant Portal | Canonical participant identity/account foundation, intake, internal profiles, qualifications/documents and review services. | Full participant self-service results/qualification/document/opportunity portal; finalized visibility, self-editing and retention rules; live delivery acceptance. | Account foundation is reusable. Agree permitted self-service actions and evidence boundaries before portal implementation. |
| 12 Central Data & Synchronization | Event-scoped offline registration, idempotent upload/downsync, review-safe identity matching, encrypted SQLite backup/recovery in 7. | Broad synchronization of later qualification/evidence, staffing, operational and financial domains; production recovery and field/network certification. | Identify actual offline use cases and data minimization first; extend explicit protocols only in an approved scope. Do not replace existing sync with generic replication by assumption. |
| 13–15 Customer and Hiring Workflows | Participant histories and qualification provenance are prerequisites; Customers navigation exists as a planned placeholder. | Customer/employer entities, grants, campaigns/jobs, candidate matching/bundles and customer portal not found. Build 13 individual scope unknown. | Establish customer ownership, hiring concepts and privacy/access rules before dependent matching and sharing. Leave Build 13 unnamed. |
| 16 HubSpot | Existing identity/intake/consent structures and ticket communications provide some reusable foundations. | Connector, stable-ID field mapping, data ownership/direction, conflicts, retries/deletion and approved campaigns. | Define a minimal explicit integration contract before any real-record transfer. No HubSpot login required for a BTW participant account by inference. |
| 17 Staff Management & Payroll | 9C staff identities, availability/confirmed assignments; 9E workdays, approved pay/per diem snapshots and individual true-mileage ledger. | Mileage policy values/effective date; per-staff permitted compensation roles; full original scope; payment/tax/payroll-provider integration not delivered. | Review residual policy and operational gaps; do not build another employee/payroll ledger. Source reports reuse approved snapshots. |
| 18 Expenses, Tasks & SOPs | 9D standalone/event tasks, assignments/status/filtering, event notes, document adapter boundary; 9F manual expenses/categories and distributed incentives. | Durable operational uploads disabled; full SOP/template/notification requirements not established as complete. | Reuse existing tasks/notes. Determine whether SOP/notification additions are needed rather than infer them from the old title. |
| 19 Billing, Reporting & Analytics | 9F official participant metrics, reconciled event cost, weighted cross-event ratios and four permissioned CSVs. | Commercial billing, invoicing/accounting/provider workflows; full original acceptance criteria; live Home analytics/updates. | Preserve financial semantics. Separate event cost reporting from a future commercial billing brief. |
| Separate 6B collection | XML original-byte archive and local index in a separate committed branch. | Score formula, ticket/phone semantics, authenticated Django delivery, event/session matching and physical device acceptance. | Preserve separate branch; collection is not accepted official score completion. Integration requires a separate reviewed contract. |

Sources: R1–R5 for delivered/not-found boundaries; H4–H9 for historical allocations. Matching a later title to current features is an analytical crosswalk, not proof that the original phase was formally rescheduled or closed.

## Current limitations to carry into planning

| Issue / gap | What must be decided or verified | Dependency boundary |
| --- | --- | --- |
| Operational document uploads disabled | Approved durable private storage, access, retention, backup and verified adapter behavior. | Must precede enabling real operational uploads; does not mean every existing participant-document workflow is disabled. |
| Mileage rate and effective date missing | Owner-approved numeric policy and effective date; no guessed Base44 tiers. | Required before configured real reimbursement approval; calculation code already exists. |
| Compensation role eligibility | Whether to add per-staff eligibility; what Set-Up Only means and any approved rate. | Business decision before changing selection/approval behavior. |
| Home tasks absent; Updates/Analytics preview-only | Which role-scoped tasks, counts, events and sources appear, and what read/unread means. | Queries, privacy and permissions must be defined before connecting live panels. A task-email/SMS system is not implied. |
| Reporting precision and date semantics | Which display precision needs adjustment; whether UTC event-list overlap and local event-start comparison filters should remain distinct. | Changes need explicit acceptance criteria; do not silently redefine historical metrics. |
| Participant totals | Cross-event figures are participant-event pairs. | Any proposed unique-person analytics must define a separate metric instead of changing the existing one silently. |
| Unknown/incomplete costs | Reconciliation, approved source snapshots, changed-source invalidation and unknown-versus-zero remain required. | Home summaries and future billing must not treat unreconciled cost as a complete total. |
| Hardware/providers/PostgreSQL | Device/network/printing, real communication delivery, concurrency and recovery acceptance need appropriate environments. | Virtual/SQLite tests and /health/ do not certify these boundaries. |
| Environment drift | Python observed 3.14.2 versus requested 3.14.3; ranged dependencies, shared older-worktree venv, ignored runtime state. | Verify before future setup; no upgrade, cleanup or configuration change authorized by this proposal. |

MoZeus reconstruction is not proposed: latest owner direction is to move away from it. Any replacement/import scope requires separate authorization. No real customer, employee or candidate records are included or needed for this review.

## Dependencies between remaining work

The following are analytical dependencies to review, not an approved critical path or numbered development schedule. Independent visual exploration need not wait for every long-term integration decision.

| Remaining capability | Prerequisites to settle | What can be reused |
| --- | --- | --- |
| Home task presentation | Audience/self versus manager scope, priority/overdue rules, density and responsive behavior. | 9D tasks/services and linked StaffProfile authorization; existing portal shell and Quick Actions. |
| Home Updates / Analytics | Defined event sources, permission filtering, freshness, counts and empty/error behavior; optional read/unread semantics. | Existing reports and source services where authorized; no placeholder values promoted to real data. |
| Participant self-service | Canonical identity/account ownership, permitted edits, consent/retention and private document rules. | Existing account, intake, qualification/evidence and review foundations. |
| Candidate matching | Hiring need definition, canonical fields/provenance and qualification meaning; explainable matching/review criteria and privacy policy. | Existing participant/assessment history, without treating assessment completion as employment eligibility. |
| Customer portal and bundles | Customer/company/users, authorization grants, visibility levels, consent and access revocation; approved shared-data scope. | Existing Django permissions and canonical participant history. |
| HubSpot communication/intake sync | BTW/CRM ID and field map; authority/conflicts; consent; retry/audit/deletion boundaries. Customer-facing dependencies only where the approved use case needs them. | Existing participant and consent records; existing operational communication patterns. |
| Expanded offline domains | Actual disconnected workflows, central/trailer authority, versioning/idempotency/conflict rules, minimal wire data and recovery. | Build 7 scoped sync/backup infrastructure; approved versioned extensions rather than assumptions. |
| XML-to-Django results | Resolve ticket versus phone contract, event/station/session identity, accepted score/formula, idempotency/authentication and review. | 6A receiving/review services plus separate 6B local evidence collection after explicit approval. |
| Billing/accounting | Customer/agreement/service definitions, approved billing events, amounts and reporting/privacy boundaries; any provider decision. | 9F event-cost reports as defined; they are not invoices, payments or a tax system. |

## Unresolved scheduling: 9G, Home, Base44 and Build 10

No option below is selected or authorized.

| Topic | Verified context | Owner decision still needed |
| --- | --- | --- |
| **Build 9G** | Earlier requests referenced a comprehensive UI pass; 9F docs allow later organization/consistency refinement without changing accounting semantics. | Whether 9G remains a distinct build, its scope, and whether it precedes or overlaps other work. |
| **Homepage improvements** | Per-user Quick Actions work; owner discussed making them smaller/more columns, fitting tasks with less scrolling, and possible Tasks / Quick Actions / Updates / Analytics areas. | Which workflow problems take priority; live sources, desktop/mobile ordering, accessibility, empty states and check-in placement. Four quadrants were a question, not approved layout. |
| **Base44 UI/UX comparison** | Existing operational application is a reference for actual workflows; future authenticated visual/workflow comparison requested. | Audit scope, target screens and how resulting observations are assigned to an approved build. No Base44 code/data import, redesign or full parity mandate is inferred. |
| **Build 10** | Historical title Stations, Quizzes & Scoring conflicts with later Home/UI discussion and an older Participant Portal allocation. Latest owner direction defers planning. | Retain/revise the historical title, assign any remaining station/quiz work, and resolve relationship to 9G/Home/audit without inventing original scope. |
| **Builds 13 and 20** | 13 only has grouped customer/hiring context; 20 is unidentified. | Whether further original-source recovery is possible. Neither missing entry is a license to assign a new title. |

## Recommended order for owner review, not development

1. **Accept or correct the historical record and its evidence gaps.** Confirm that multiple numbering schemes remain distinct. Keep unresolved 13/20 entries visible; absence need not block every independent future decision.
2. **Agree the scope crosswalk and residual requirements.** Review what 7/9C–9F delivered early, what later titles still require, and whether any historical requirement is deliberately retired. Record explicit decisions before treating a phase as complete or renumbered.
3. **Resolve the next review boundary for 9G / Home / Base44 / Build 10.** Recommended next activity, only after approval: a bounded read-only screen/workflow comparison and prioritized gap report. Decide scheduling after that review; do not begin implementation or presume a four-area layout.
4. **Review policy and readiness gaps relevant to the selected work.** Mileage/roles if staff flows change; storage before enabling uploads; metric/permission semantics before live Home analytics. Unrelated gaps need not block an independent UI discussion.
5. **Review participant, customer/hiring and integration dependency contracts.** Agree portal/privacy/grants and matching meaning; define minimal CRM/offline/hardware/billing contracts when their respective work becomes a priority. This is a discussion order, not a mandate to build every integration first.
6. **Approve one concrete next build brief.** Include verified baseline, exact scope/exclusions, acceptance tests and visual review, data/privacy boundaries and explicit commit/push/deployment authority. Reinspect state before any authorized code changes.

## Preserve the established development process

The owner supplies scoped numbered build briefs. Codex inspects source/Git/docs and reusable workflows, resolves missing business rules honestly, implements only approved scope and uses synthetic isolated verification. Acceptance normally includes focused tests, full project runner, Django checks, migration drift/diff checks, populated migration preservation when relevant, permission boundaries, desktop/mobile review and actual exports. New fixes receive appropriate regression checks; hardware and provider acceptance remain explicit.

Use the existing isolated branch/worktree practice after authorization; preserve dirty/untracked/ignored data and separate simulator/UI work. Verify actual directory, branch, HEAD and status rather than trust the older Explorer folder. No cleanup, reset, stash, branch switch/creation or merge is proposed by creating these drafts.

Commit, push, merge and deploy approval is specific to the current request. Pushing render-preview triggers deployment and must be treated as such. Future authorized publishing requires checking ancestry/newer changes, normal approved push, Render Live/exact SHA, migration logs, health and deployed workflows. No force-push, main/production merge, infrastructure change, dependency upgrade or next build is implied. Stop after the approved checkpoint/report.

The account/workspace transition does not justify redesigning architecture, hosting or tooling. Preserve Django custom identity, Groups/Permissions, canonical domain records, explicit audits, financial snapshots, official/test isolation, private storage rules and shared server-rendered portal components.

## Decisions requested for a future review

- Are the recovered historical facts and uncertainty labels accurate?
- Which original sources can still resolve Builds 13/20 and the numbering conflicts?
- Which later requirements are satisfied, still needed, deliberately deferred or deliberately retired? No retirement is assumed here.
- Should the next authorized step be a bounded read-only Base44/Home/portal audit, and how should 9G/Build 10 relate to its findings?
- What is the next concrete implementation brief, its acceptance criteria and publishing authority? No answer is presumed.

No application tests, migrations, deployment, data export or real-record access were performed for this proposal. Drafts remain local and uncommitted pending owner review and explicit preservation approval.
