# Build 11B — Smart Intake Forms, Field Mapping & Participant Integration

Date: October 22, 2026. **Local owner-review candidate — not accepted, committed, pushed or deployed.**

## 1. Executive summary and scope completion

Build 11B is implemented locally as a review candidate on top of the unaccepted Build 11 checkpoint. It adds the shared four-step intake, protected profile projection, Participant Review Queue, independent versioned forms, Admin configuration, and explicit trailer installation. No operational form was created or activated outside isolated synthetic test databases.

The owner resolved the material identity checkpoint: **Use Associate & Update; defer true record merges.** No destructive merge or automatic rewriting of established participant, ticket, document, assessment, or verification relationships was added.

The implementation is ready for owner review, subject to the verification limits below. It is not an accepted or deployed release. Minor-specific legal/operational consent review and separate Build 11 acceptance remain release prerequisites.

Repository: `zeke-chapman-btw/built-to-work`. Worktree: `/workspaces/btw-build-11b`. Local branch: `build-11b-smart-intake`. Foundation and unchanged HEAD: `c3b792ef0692f13a3b3904159926f1934eedf52f`. Implementation changes are unstaged and uncommitted.

## 2. Identity safety review findings

The mandatory pre-implementation checkpoint is retained separately in `DOCS/BUILD_11B_IDENTITY_SAFETY_REVIEW.md`. Its prospective wording records the historical checkpoint; this completion report records what was subsequently implemented.

Existing matching helpers accept one contact in other workflows. Those shared caller semantics remain unchanged. Build 11B uses its own explicit dual-contact resolver and the existing canonical identity-link read projection.

Both valid email and phone are mandatory at the smart-intake service boundary. Contact columns remain nonunique so legitimate shared family contacts remain possible. Sorted contact-lock records serialize overlapping submissions on PostgreSQL; the submission operation UUID and transaction locks make retries repeat-safe.

A matching contact is association evidence. It creates no account, session authentication, portal access, employer eligibility, or public access to the matched profile. Anonymous completion contains no participant identity or matched-profile details.

Legacy/current values with uncertain provenance are protected conservatively. An intake-owned value may change only while its last-written value still matches, it has not been marked manual/reviewed, and the incoming source is not older. Ordinary participant/career saves revoke automatic ownership for the fields actually changed. Explicit manual Unknown remains different from No and from an unanswered intake.

## 3. Field-level mapping and update-precedence matrix

The following describes the implemented contract and precedence. Retained submission answers are validated snapshots; email/phone are normalized. Their original submitted version and answer states remain separate from the current profile. Blank, omitted or not-applicable answers do not clear current values. Existing uncertain legacy values are protected, including a legacy blank DOB. The new structured career data lives in the existing one-to-one career profile; common hands-on entries are separate from actual employment.

| Field group | Canonical storage / extension | Source of truth and automatic-update policy | Manual/review protection | Omitted/unknown | Duplicate/offline behavior |
|---|---|---|---|---|---|
| Name / preferred name | Existing Participant names | New person: validated values. Associated: material name conflicts require review; only eligible intake-owned formatting/preferred values may update | Legacy/manual name protected; explicit field ownership required | Optional preferred omission does not erase | Same resolver at central import; retained original names |
| DOB | Existing Participant.date_of_birth + derived minor status | New validated DOB, >=14 at occurrence; differing known DOB always review | Never silently overwrite existing DOB; full DOB separately restricted | Required on new full intake; legacy absence not invented | Recompute age from original occurrence, not delayed import date |
| Email / phone | Existing normalized contact fields, separate from login | Mandatory valid values; dual contact is association policy only; changed contact goes through review | No account/login edits; preserve reviewed/manual contact | No exceptions/blank bypass | Shared-family and cross-contact ambiguity reviewed; stable UUIDs retained |
| Street / city / state / ZIP | Existing Participant address/location | New: map validated values. Associated: eligible intake-owned current values only | Legacy/manual values protected; stale last-written hash blocks update | Optional street omission preserves; city/state/ZIP required | No last-arrival-wins; occurrence/provenance ordering and conflict review |
| Education / current situation | Existing life_stage plus additive structured career education | Approved stable codes and optional school/training fields; version-aware legacy meaning | Staff values protected; no old working-code reinterpretation | Prefer not to answer remains explicit; hidden data not mapped | Same contract and per-field provenance |
| Employment history | Existing ParticipantWorkExperience, additive source/entry/duration provenance | Reported current role/responsibilities/duration may append a source-owned work row. Optional prior-history text stays in retained intake/current structured data; no invented dates/employers or automatic parsing | Never edit/archive staff rows automatically | Optional job/history omission adds nothing | Submission + stable entry UUID repeat safety; no duplicate employment table |
| Hands-on experience | Necessary participant-owned common entry extension; existing catalog skill references | Append common paid/unpaid school/project/work entries with duration and source | No assessment/verification inference; staff corrections protected | Yes requires >=1 complete; extra entries optional, no arbitrary cap | Stable source entries/skills; no overlap summing into overall years |
| Industry interests | Additive controlled career selections, legacy text retained | Max3; Not sure exclusive; eligible intake-owned current preferences | No mapping to practiced skill/verified qualification | Unknown/omission does not erase | Contract-version choices, not current labels |
| License / certifications | Existing catalog/ParticipantQualification plus response/class summary extension | Exact self-reported positive claims; license status not a verification decision | Never overwrite/revoke reviewed claims/evidence or existing verified facts | None/No/Not sure are answers, not fake credential records | Frozen mapping + per-effect provenance; retries do not duplicate |
| Career timeline | Additive controlled career field | Required approved timeline; eligible intake-owned update | Manual timeline protected | No inferred employment suitability | Original occurrence/order and source retained |
| Opportunity types / priorities | Additive controlled career multivalues | Opportunity >=1; priorities optional <=3; no ranking | Manual selections protected | Hidden Other detail/omission does not clear | No lossy conversion to legacy single preferred_work_type |
| Desired pay | Additive optional career range | Only age18+ AND not high-school; categorical self-report | Manual values protected; not payroll/offer | Hidden stays not_applicable, no zero | Frozen codes and source; no backfill |
| Travel willingness / range | Additive Yes/No/Maybe enum + separate range; legacy boolean retained | Required willingness; range only Yes/Maybe. Travel != relocation | Manual values protected; no inferred old False/unanswered distinction | Unknown distinct from No; hidden range not overwritten | Same four-state semantics offline |
| General updates | Participant-level optional current preference with source/time/version history | Explicit Yes/No from trusted associated submission, subject to manual protection; no messaging automation | Retain attributed staff preference; no legal-consent conflation | Unanswered not No and not Yes | Chronological idempotent preference changes; ticket opt-ins separate |
| Referral / original acquisition | Existing ParticipantAcquisition + retained source context | Set origin only on genuinely new person: event-linked=Event, general online=Online, unresolved evidence=Unknown | Existing/corrected origin never automatically overwritten | Unknown remains until evidenced staff correction | Original occurrence and event refs, not receipt time or inferred attendance |
| Staff-reviewed qualifications/evidence | Existing qualifications/documents/links/append-only verification | Intake adds separate self-report provenance only | Always protected; no automatic verified decision, evidence replacement or score change | Omission/negative answer cannot delete evidence | Original source UUID/IDs and verification snapshots retained |

Current profile scalar values project onto the canonical person. Source-owned consent, work, hands-on and qualification evidence retain their original source participant/submission relationships and are aggregated through the existing reviewed identity links. Qualification selections create catalog claims only when an explicit approved mapping was configured and frozen; unmapped selections remain retained self-report answers.

Existing staff career fields retain precedence when manually changed. Interests/travel discovery and corresponding intelligence segmentation read the effective value, including Maybe and explicit Unknown. No new hiring score or inferred experience total was added.

## 4. Participant matching and staff review verification

The verified rules are:

| Contact/identity condition | Implemented outcome |
|---|---|
| One email candidate and one phone candidate, same active person, compatible material name and known DOB | Automatic association |
| Only one contact matches | Retain a provisional source and queue review |
| Contacts match different people | Queue review |
| Multiple raw matching records | Queue review; do not silently collapse ambiguity |
| Neither contact matches | Create a new participant |
| Material name or known-DOB conflict, archived identity, or conflicting/circular association | Queue review |
| A unique, unambiguous reviewed alias | Follow the existing canonical link, then apply canonical name/DOB/archive checks |
| An existing portable source UUID conflicts with another established person | Preserve the source; true established-record merges remain deferred |

Possible duplicates finish intake without being attached to an uncertain existing person. Event registration can remain held separately, including when event capacity is full. The public smart-intake entry point also permits this review path; it does not override capacity for an ordinary confirmed new registration.

The queue implements Associate & Update, Link Only, Keep Separate, and Review Later with permissions, explicit target selection where applicable, conflict comparisons, reason, confirmation, optimistic revision, and attributable decision history. Full DOB requires the additional DOB permission or an active superuser. No ordinary staff access was automatically granted.

Associate & Update applies eligible unreviewed answers through the existing identity association facility. Link Only preserves current profile values. Keep Separate retains the distinct source and its original provenance. Review Later preserves the pending case. Existing established sources cannot be linked wholesale to another person through these actions.

Registration/ticket conflicts remain visible and held; an authorized retry preserves the identity decision and original identifiers. A later authenticated trailer message may supply original ticket evidence, but cannot silently replace a central identity decision. Browser verification saved a synthetic Associate & Update decision and confirmed that the staff-reviewed city and established contact/identity remained intact.

## 5. Independent form/version architecture

The existing RegistrationForm, RegistrationFormVersion, RegistrationQuestion and RegistrationSubmission models remain the foundation. Managed smart forms are explicitly identified; legacy forms and their histories are not converted or replayed.

Each form has its own history. Editing a published version creates the next draft in that form. Further edits to an unpublished draft use revision checks. Duplicating creates a separate unpublished Version 1 with copied configuration and no copied submissions. Published contracts include frozen question definitions, typed destinations, choices, conditional rules, qualification mappings, mapping provenance, a portable version UUID, and a content hash.

Only allowlisted profile destinations and compatible typed rules are accepted. Mandatory identity/consent fields and common experience structure cannot be weakened. Repeatable experience entries have stable entry UUIDs and no configured product entry-count cap. Existing HTTP/framework resource limits still apply.

Publishing and assignment are separate operations. A published form does not replace another channel's active form. An Admin explicitly chooses a published version for general online, event-linked, staff-assisted, or event/trailer scope. Retiring stops new starts while allowing pinned in-progress sessions and valid queued historical versions.

The browser review verified that editing published V1 created draft V2 while the published V1 assignments and trailer activation remained V1.

## 6. Admin permissions and publishing verification

The established Admin boundary is an active user with both staff and superuser status. No replacement role system, new authentication backend, or broad permission grant was introduced.

Admin-only workspace screens provide form creation, history, question wording/choices/order, supported conditions, approved trade-skill relationships, qualification mappings, duplication, preview, publishing, assignment and retirement. Routine configuration uses labeled controls rather than requiring raw JSON.

Preview uses synthetic Alex (17, high school/CTE), Jordan (22, retail/electrical interest), and Chris (43, welding supervisor) examples. It writes no participant/session/submission and supplies no operational legal consent text. The browser review confirmed that the minor preview hides pay and the adult non-high-school preview exposes the optional pay range.

Raw Django Admin boundaries also restrict managed form/version/question mutation and sensitive submission access. Submitted smart-intake evidence and published contracts are guarded against ordinary model/Admin edits or deletion. The new `participants.view_participant_dob` permission is not automatically assigned to staff.

Source: `intake_config_forms.py`, `intake_config_views.py`, `intake_lifecycle.py`, `intake_admin_guards.py`, `intake_model_guards.py`, and `participants/admin.py`.

## 7. Trailer synchronization and in-progress-form protection

Trailer intake uses the same four-step Django wizard on the trailer's local application and database. This is cloud-disconnected local-server operation, not a newly introduced browser-offline PWA.

A cloud publication never automatically downloads or activates smart form definitions. Existing automatic pre-registration downloads strip the smart contract and require the exact previously installed portable version before importing its submissions. If a new event configuration is staged before its form exists locally, an Admin can prepare event/consent information, then explicitly install the approved form and retry the held pre-registration download.

An authenticated local Admin checks the scoped cloud inventory, reviews an offered version, and explicitly confirms download. The package is validated and installed atomically. A historical version can be installed for retained submissions without becoming active. Validation, endpoint, scope or installation failure preserves the previous active version and records an actionable error.

Sessions pin the original form UUID/hash, consent evidence, context and answers. A session started on V1 completes on V1 after a newer version is activated. The automated installation test verifies this equivalent of the requested V2-to-V3 scenario. Retired-version queues remain acceptable when otherwise valid.

Portable transport uses form/version UUID, slug/version/hash, consent key/version/hash, source identity UUID, operation UUID, original timestamps and registration/ticket UUID/token references. It does not assume trailer-local integer keys equal central keys. It reuses the existing authenticated device ingestion, transactional outbox, receipts, reserved ticket ranges and reconciliation facilities.

The live browser failure-path check intentionally had no central download URL/token configured in its isolated environment. It displayed the configuration error while retaining active V1. Actual cloud-to-physical-trailer networking, power-loss recovery and hardware were not exercised.

## 8. Online/offline parity results

| Area | Evidence and result |
|---|---|
| General online | Shared typed contract; four browser steps completed with synthetic data; generic completion |
| Event-linked | Same contract plus separate trusted event context, custom questions/group rules and ticket delivery choices; event origin is not attendance |
| Staff-assisted | Same mandatory contact/age/consent rules; existing staff and participant/event permissions required |
| Trailer local | Same contract and service; pinned local activation, reserved-ticket/outbox tests, pending-duplicate path |
| Delayed central import | Original source UUID/version/answers/consent retained; fresh central matching and chronology-aware current projection |
| Retries and changed retry payloads | Idempotent equivalent operations; conflicting retry evidence rejected or held through the appropriate receipt/review path |
| Retired/updated forms | Valid pinned or queued versions accepted; no reinterpretation using today's mapping |
| Manual values and Unknown | Protected-field and manual-travel tests, including explicit clearing to Unknown |
| Experience and qualifications | Common repeated paid/unpaid entries remain separate from actual employment; only explicit positive frozen mappings create self-reported claims |
| Original acquisition | New trusted event intake = Event; new general online = Online; otherwise Unknown; established/corrected origin is preserved |

Optional or hidden answers do not erase current values. Interests are not practiced skills, travel is not relocation, a duration range is not an inferred exact date or overall years of experience, and a registration is not attendance.

Existing manual participant creation and legacy forms retain their established Build 11 rules. The mandatory dual-contact rule applies to every Build 11B smart-intake channel; it is not a retroactive requirement on all historical or manual records.

## 9. Migration and historical preservation evidence

Three additive migrations were generated and exercised on SQLite and PostgreSQL:

- `apps/events/migrations/0014_participantmatchreview_candidates_and_more.py`
- `apps/participants/migrations/0016_intakecontactlock_intakefieldstate_and_more.py`
- `apps/participants/migrations/0017_alter_intakefieldstate_source_submission_and_more.py`

They extend the existing review/submission/career/work records and add assignments, trailer activations, pinned sessions, contact locks, field provenance and common hands-on experience. Existing migrations were not edited. No RunPython historical backfill, answer replay or destructive data operation was added.

An unchanged source export of the exact Build 11 checkpoint was migrated in a separate synthetic PostgreSQL database. Synthetic participant, career, legacy form/submission, consent, acquisition, employment, note, qualification, event/registration/ticket, trailer and reconciliation records were seeded before upgrading. All original columns, rows and identifiers were fingerprinted before/after.

**Result: 730 existing rows across 119 original tables preserved; no changed original table fingerprints.** Newly added tables/columns and newly created migration/permission rows were assessed separately from the retained original rows.

This is seeded migration evidence, not a backup or a claim to have inspected confidential operational records. The seeded preservation fixture did not populate every assessment/private-file scenario. Their existing regression tests ran in the broader suite. Operational databases, Render data and production were not migrated.

The first preservation harness attempt failed before custom seeding because a Python namespace package had no file path. The corrected harness used the settings module path and completed successfully; this was a harness issue, not an application migration failure.

## 10. SQLite/PostgreSQL and visual test results

| Verification | Result |
|---|---|
| Final full SQLite suite | **1,136 tests; OK, 9 skipped** (four new concurrency tests require PostgreSQL; five existing skips) |
| Final focused PostgreSQL intake suite | **71 tests; all passed**, including four concurrency cases and delayed older offline answers |
| Broader PostgreSQL suite | 1,135 tests run; **20 errors**, not a passing suite |
| Unchanged Build 11 PostgreSQL baseline | 1,065 tests run; **the exact same 20 error test IDs**, no additional error cases in the broader 11B run |
| JavaScript syntax | New intake browser script passed syntax check |
| Existing JavaScript regression scripts | Notification bell, shell/focus, task modal, shared 10C disclosure controls and Duck Hunt logic passed |
| Django system check | No issues |
| Migration consistency | No ungenerated changes |
| Diff whitespace check | Passed |
| Synthetic pre/post-migration preservation | 730 original rows / 119 original tables unchanged |
| Regular Chrome | Representative desktop, tablet and mobile review completed on isolated port 8028 |

The broader PostgreSQL run preceded the final additional delayed-arrival test; that case is included in the final passing 71-test PostgreSQL suite and 1,136-test SQLite suite. Results are separate runs and must not be added together as unique tests.

The 20 inherited PostgreSQL errors comprise 15 communications cases reporting a closed connection and five backup cases that explicitly support SQLite only. The first communications failure executes the existing delivery command, which calls `close_old_connections()` inside the test context. The same case IDs fail on an unchanged source export of the exact Build 11 checkpoint. This comparison does not certify those older workflows as production-verified on PostgreSQL.

During implementation, PostgreSQL exposed nullable outer-join row-locking errors and a concurrent-session stale related-object cache. Those new-code issues were corrected with explicit row-lock targets and a fresh submitted-record lookup. Final focused concurrent tests pass. A legacy offline-registration test-double compatibility issue was corrected and the final full SQLite suite passes. Initial harness/setup failures remain distinct from final application results.

Browser checks covered all four steps, error-summary focus, conditional employment fields, experience add/remove with focus, travel Maybe/range, generic completion, a synthetic Associate & Update decision, staff-value preservation, published-edit-to-new-draft behavior, minor/adult previews, and missing-endpoint trailer fallback. Representative widths were 1440 desktop, 820 tablet and 390 mobile; normal browser sizing was restored. Representative mobile/tablet screens showed no page-level horizontal overflow; wide comparison/configuration tables use local horizontal scrolling.

All reviewed people, contacts, consent, events and tickets were synthetic. No real message provider or operational cloud download was exercised. Physical trailer/hardware, actual outages and a timed ten-minute human study remain unverified.

Machine-readable receipts: `acceptance-verification-receipt.json`, `visual-review-receipt.json` and the local report's screenshot gallery. Full logs remain in the isolated `/tmp/btw-build-11b/` verification directory. These local evidence artifacts are outside the Git file set.

## 11. Changed files, branch/worktree state, and protected-state receipt

The candidate stays on local branch `build-11b-smart-intake` in `/workspaces/btw-build-11b`, with unchanged HEAD `c3b792ef0692f13a3b3904159926f1934eedf52f`. The index is empty. There is no new implementation commit and this branch has not been pushed.

All existing local branch references match the preflight. The only added local branch is this isolated Build 11B branch. Remote `render-preview` remains `c90baf984d621852b2fb847141fd28cf1d7ef0a2`; the source Build 11 branch remains `c3b792ef0692f13a3b3904159926f1934eedf52f`; the historical migration-handoff branch remains `6c3ced07f85779e4f6bd1234d4cde3ec1ede65b2`. The older dirty Render checkout was not reset, cleaned, switched or updated.

All **27 pre-existing worktrees** retain their preflight HEAD, branch and Git status. The tracked/index/untracked-content aggregate fingerprint matches for **26 worktrees**, including the dirty main/6B simulator work and dirty old Render checkout.

**Historical audit limitation:** the Build 9F worktree's aggregate content fingerprint could not be reproduced from the retained preflight receipt. It includes an unusually quoted pre-existing filename; the reason for the aggregate difference was not conclusively established. All ten untracked migration-handoff documents in that worktree are independently byte-identical to preserved commit `6c3ced07f85779e4f6bd1234d4cde3ec1ede65b2`. Its HEAD, branch and complete Git status are unchanged. The older oddly named file predates this work; no cleanup, restoration or write to that worktree was attempted. Do not interpret this receipt as proof of every byte in that one historical checkout.

No production service/database was accessed or changed. Dependencies, application configuration and hosting were unchanged. The isolated test runtime consists of temporary SQLite databases, an own local PostgreSQL container/databases, and the synthetic development server on private port 8028. Existing development servers/containers were not repurposed or stopped.

The final candidate contains **71 changed/new paths**, including this report. They are all Build 11B application, tests, styles/templates, additive migrations or documentation. Output reports, screenshots, temporary test harnesses, credentials and databases are not in the repository file set. A pattern scan found no private-key blocks, recognized access-token formats or credential-bearing URLs in the intended source mirror; test credentials in test fixtures are synthetic, not operational secrets.

```text
DOCS/BUILD_11B_IDENTITY_SAFETY_REVIEW.md
DOCS/BUILD_11B_IMPLEMENTATION_AND_VERIFICATION.md
apps/events/downsync.py
apps/events/downsync_views.py
apps/events/migrations/0014_participantmatchreview_candidates_and_more.py
apps/events/models.py
apps/events/offline.py
apps/events/offline_views.py
apps/events/public_views.py
apps/events/reconciliation.py
apps/events/reconciliation_views.py
apps/events/sync_views.py
apps/internal_ui/navigation.py
apps/participants/admin.py
apps/participants/apps.py
apps/participants/discovery.py
apps/participants/identity.py
apps/participants/intake_admin_guards.py
apps/participants/intake_config_forms.py
apps/participants/intake_config_views.py
apps/participants/intake_contracts.py
apps/participants/intake_defaults.py
apps/participants/intake_discovery.py
apps/participants/intake_identity.py
apps/participants/intake_lifecycle.py
apps/participants/intake_model_guards.py
apps/participants/intake_models.py
apps/participants/intake_projection.py
apps/participants/intake_review.py
apps/participants/intake_signals.py
apps/participants/intake_submission.py
apps/participants/intake_sync.py
apps/participants/intake_trailer.py
apps/participants/intake_trailer_views.py
apps/participants/intake_views.py
apps/participants/intake_wizard.py
apps/participants/intake_workspace.py
apps/participants/intelligence.py
apps/participants/migrations/0016_intakecontactlock_intakefieldstate_and_more.py
apps/participants/migrations/0017_alter_intakefieldstate_source_submission_and_more.py
apps/participants/models.py
apps/participants/qualification_services.py
apps/participants/registration_views.py
apps/participants/test_intake_concurrency_11b.py
apps/participants/test_intake_contract_11b.py
apps/participants/test_intake_identity_11b.py
apps/participants/test_intake_lifecycle_11b.py
apps/participants/test_intake_profiles_11b.py
apps/participants/test_intake_submission_11b.py
apps/participants/test_intake_trailer_11b.py
apps/participants/test_intake_ui_11b.py
apps/participants/urls.py
apps/participants/workspace_forms.py
apps/participants/workspace_models.py
apps/participants/workspace_views.py
static/internal/css/intake_workspace.css
static/participants/intake.js
templates/participants/intake_complete.html
templates/participants/intake_config_detail.html
templates/participants/intake_config_edit.html
templates/participants/intake_config_index.html
templates/participants/intake_config_preview.html
templates/participants/intake_internal_base.html
templates/participants/intake_profile_summary.html
templates/participants/intake_review_detail.html
templates/participants/intake_review_queue.html
templates/participants/intake_start.html
templates/participants/intake_step.html
templates/participants/intake_trailer.html
templates/participants/intake_trailer_prepare.html
templates/participants/staff_profile.html
```

## 12. Development Workflow Continuity

This continues the same application, repository, Codespace, Django architecture and staged build process. The ChatGPT Business account transition does not authorize a redesign, hosting migration, tool replacement or changed release policy.

The owner supplies a scoped build brief with objectives, approved behavior, exclusions, testing and stopping point. The agent verifies the exact foundation and protected state, reads existing conventions and evidence, isolates the work, and implements only the authorized scope. Material identity/data-policy conflicts stop the affected workstream for owner direction. That checkpoint occurred here and was resolved by the Associate & Update decision.

Implementation proceeds through independently testable packages, synthetic fixtures, safe local migrations, regression checks, and browser review against the established Build 10C workspace standard. Issues found during testing are investigated and corrected within scope; inherited excluded work is documented rather than silently repaired. Reports distinguish passed, failed, inherited, unverified and deferred work.

Owner acceptance is separate from implementation. GitHub preservation requires explicit authorization to stage/commit/push the reviewed file set. Render Preview release requires separate explicit authorization, source/baseline verification and a controlled update of the deployment branch, followed by automatic deployment, migration/health checks and available browser review. Production requires its own authorization. No such preservation or deployment authorization was exercised in this build.

New sessions must read the actual repository and current handoff/build reports, confirm branch/HEAD/dirty state and existing worktrees, and resume this isolated candidate. The current implementation is not recoverable from GitHub alone until the owner authorizes its preservation. GitHub/account/Codespace access and ownership remain separate from chat history.

## 13. Known limitations and unresolved risks

- **Minor-specific legal/operational consent review is a release blocker.** Synthetic test consent is not approved operational wording.
- Build 11 remains a separately unaccepted foundation. This report does not accept it.
- True merges of established participant records are deferred. Some established-source conflicts must remain pending or be reviewed against their retained identity.
- The inherited PostgreSQL ticket-allocation race is not repaired. Event/trailer registration still depends on that service. A concurrent allocator failure can roll back a registration; the separate held/retry paths do not constitute an allocator repair.
- The full PostgreSQL suite is not green: the same 20 communications/SQLite-only-backup cases fail on the unchanged baseline. The focused new intake/concurrency suite passes. The report does not hide or reclassify those full-suite failures as passing.
- No physical trailer, kiosk hardware, simulator hardware, live central/trailer network transfer, actual message delivery, or timed ten-minute human usability study was performed. Email/network transports were kept inactive or mocked in isolated verification.
- No historical forms/answers are backfilled or replayed. No operational smart form is implicitly activated by installing this code. Admin form review, consent selection and explicit scoped assignment remain rollout actions.
- Current profile displays retain the most recent 50 intake references and up to 100 common experience entries in that view; underlying records remain stored. These display bounds are separate from the uncapped repeat-entry intake contract.
- The repository preservation receipt has one historical byte-fingerprint coverage gap described in section 11. No repair or cleanup of that protected worktree was attempted.
- Existing legacy manual fields remain alongside new structured fields. Their precedence is intentionally conservative; new intake does not infer hiring suitability, employment interest, experience totals or verification from unrelated answers.

Kiosk text editing/static Admin kiosk previews remain the owner's deferred future work. No kiosk screen, CRM, employer matching, portal/authentication expansion, scoring, dependency, hosting or application configuration change was introduced.

## 14. Build 11 acceptance dependencies

Build 11B was explicitly authorized for development before final Build 11 acceptance. The foundation remains the exact candidate `c3b792ef0692f13a3b3904159926f1934eedf52f`.

Before an integrated merge or release, the owner must separately review and accept Build 11, review this Build 11B candidate and its inherited limitations, resolve release prerequisites, and authorize the exact preservation/deployment operation. No branch merge, rebase, push, Render update or production operation is implied by the local verification results.

## 15. Minor-consent release prerequisites

Before operational activation, the owner must complete the required minor-specific legal and operational review, approve the actual versioned consent documents and their applicability to the allowed ages/channels, and confirm the applicable staff handling/access process.

The implementation enforces minimum age 14, derives age at the original intake occurrence, keeps DOB access restricted, requires an approved versioned consent at submission, and hides/rejects pay answers for under-18 or high-school participants. Those technical safeguards do not establish the missing legal/operational policy.

No guardian workflow, new consent wording, or implied legal approval was invented. The fixture consent used in browser/tests was clearly synthetic and confined to isolated databases. There is no authorization to activate it operationally.

## 16. Explicit owner acceptance checklist

- [x] Implement the authorized four-step smart intake and typed conditional validation.
- [x] Implement dual-contact identity safety and the owner-approved Associate & Update terminology.
- [x] Implement all four review actions, protected projection, permissions, revision checks and audit history.
- [x] Implement independent frozen versions, duplication and Admin-only management/preview/publishing.
- [x] Implement explicit trailer installation, pinned sessions, retained historical versions and safe failure paths.
- [x] Exercise SQLite/PostgreSQL, concurrent operations, delayed imports, retries and synthetic preservation.
- [x] Review representative desktop, tablet and mobile screens in regular Chrome.
- [x] Keep implementation isolated and leave changes unstaged/uncommitted; no push or deployment.
- [ ] Owner reviews the final report, matrix, source diff and screenshots.
- [ ] Owner separately accepts Build 11 and Build 11B.
- [ ] Owner completes minor-consent release prerequisites and approves operational form configuration.
- [ ] Owner reviews inherited PostgreSQL limitations and the historical preservation-audit gap.
- [ ] Owner explicitly authorizes the reviewed GitHub preservation file set.
- [ ] Owner separately authorizes any controlled Render Preview release; production stays outside this authorization.

Stop after local verification and reporting. Await further owner instructions.
