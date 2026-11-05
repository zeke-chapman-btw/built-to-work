# Build 16 — Unified relationship management

Owner authorization: November 5, 2026. Integration baseline: `f017bbb490c9a8f3300b913365bf11832c6a4a6b`.
Development branch: `build-16-crm-communications`; isolated worktree: `/workspaces/btw-build-16-crm-communications`.
All 78 pre-existing worktrees and their indexes, untracked work and branch references are protected.

## Focused repository audit and implementation matrix

| Existing capability / verified source | Gap | Build 16 extension | Privacy / verification |
| --- | --- | --- | --- |
| Canonical Participant and reviewed identity links (`participants.identity`, `workspace_views`, `workspace_access`) | General interaction types, chronology and follow-ups are not one structured workflow | Embed separate BTW Interactions and Employer Interactions in the existing staff profile; retain canonical IDs and existing sections | Reuse private note permissions; recheck fresh staff identity; preserve alias history and all profile/evidence boundaries |
| `ParticipantNote`, existing note correction/archive and audit | Existing notes are not calls, texts or provider events | Present existing notes as attributed legacy sources without copying them; new typed interactions have one authoritative record | Preserve original note permissions and existing editor/history links; do not invent provenance |
| Customer organizations, contacts, primary-contact pointer, relationships, Leads/Members and scoped customer services | Contact role/method-preference metadata and structured relationship history | Optional one-to-one metadata over the existing contact; reuse the native primary-contact pointer and customer service; company/profile activity over existing relationships | Contact metadata creates no authenticated identity or membership; unknown preferences stay unknown and are not consent |
| Build 15 `CandidateOutreach`, `CandidateOpportunity`, opportunity delegation | General participant/customer touchpoints and employer-authored history | Reuse outreach as a historical projection, with scoped engagement associations for new activities | Preserve opportunity owner/collaborator authority and existing recruiting history; do not advance stages |
| `CandidateDisclosure`, `customer_grant`, `valid_disclosure`, scoped response permission | Employers cannot maintain an attributed interaction history | Extend the existing disclosed-candidate detail using manual employer activity records | Every read/write uses current membership and exact disclosure; withdrawal removes employer access; BTW retains scoped history; no internal records or Tasks reach employer templates |
| Operational Tasks, `save_task`, TaskParticipantLink, RecruitingTaskLink, Task comments/history and notifications | Interaction/customer references and manual follow-up entry | Link to the same Task records and creation service; explicit staff action only | Existing assignee/creator/administrator Task access and normal Task notification behavior remain authoritative |
| Explicit core `AuditLog` and recruiting policy transaction/locks | Structured activity revisions and idempotency | Stable UUIDs, occurrence/creation timestamps, operation fingerprints, optimistic revisions and retained snapshots | Serialize with existing group/identity/recruiting policy locks; test stale edits, replays, revocations and foreign organizations |
| Ticket/operational communication queues and existing opt-ins (`events.communications`) | These are not a general staff CRM or employer sender | Manual email/SMS/call provenance; staff external compose action; future EZ Texting reference fields | No queue, webhook, API sender, OAuth, mailbox reader or marketing send; no inferred consent or provider-confirmed status |
| Shared staff/customer shell, Jost, 920px ordinary content and responsive editors | Embedded CRM affordances and compact forms | Reuse the accepted shell and domain navigation; lightweight cross-record CRM support | Desktop/tablet/phone, keyboard, errors and overflow; portal/kiosk/native-admin isolation |

The migration handoff is the preserved Build 9F historical snapshot, not a current feature inventory. Current source, later build evidence and the present Build 16 brief govern implementation. No applicable AGENTS.md was found in repository or relevant ancestors.

## Data and access decisions

`crm.Interaction` is one typed activity foundation with immutable audience, ownership, original author, associations and provenance. BTW-owned and employer-owned records use separate service queries and UI histories. `InteractionRevision` retains attributed snapshots; `InteractionTaskLink` references the existing Task engine. `ContactRelationship` adds optional role/preference metadata to an existing `CustomerContact`, not another contact directory or login system. Primary business contact remains the existing authoritative `CustomerOrganization.primary_contact` field and customer service; no competing primary-contact flag is stored.

BTW participant activity reuses existing private-note read/create authority. General company relationship history uses existing Customer Manager authority. Recruiting-scoped records additionally enforce existing opportunity/candidate delegation. Employer creation/correction uses Build 15's existing response authority: Leads or Members with explicit disclosure-scoped response permission. Current authorized company users can read only the valid disclosed-candidate history. An author or authorized Lead may correct an employer entry, while immutable authorship remains retained. BTW staff review employer history read-only through existing recruiting authority.

Revoked disclosures, suspended memberships, invalidated approved projections, consent withdrawals, exclusivity and identity changes retain the existing fail-closed policy. Employer history remains stored but ceases to be available through employer routes. No CRM operation selects a candidate, confirms a hire, releases information, alters exclusivity, or writes to participant-facing history.

Contact preferences mean a staff-recorded method preference or suppression, with attributable notes. They are not marketing consent, candidate-sharing permission, or evidence of a legal acceptance. No preference is inferred for existing contacts.

## Phase checkpoints

- 16A: verified foundation. 21 SQLite tests passed; 27 PostgreSQL 18 foundation/concurrency tests passed, zero skips. Populated migration verification preserved all 177 baseline tables (metadata additions allowed), added only four CRM tables, and passed original-baseline ORM inserts after upgrade. System, migration-drift and whitespace checks passed. This is the phase checkpoint, not final integrated acceptance.
- 16B: participant/customer workflows pending.
- 16C: employer candidate interactions pending.
- 16D: external compose/manual communications and Task integration pending.
- 16E: integrated tests, PostgreSQL/migration preservation, browser acceptance and Preview release pending.

No Production, HubSpot, outbound delivery, mass marketing, new CRM automations, new CRM notifications or automatic follow-up Tasks are authorized.
