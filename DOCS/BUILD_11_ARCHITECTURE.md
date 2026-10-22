# Build 11 — Participant workspace architecture and boundaries

Baseline: c90baf984d621852b2fb847141fd28cf1d7ef0a2. Development branch: build-11-participant-workspace. No merge/deployment is included.

## Shared identity and evidence

Participant (integer PK, immutable identity_uuid) and its existing one-to-one ParticipantCareerProfile remain the source of current identity/career data. Build 11 adds four blank/nullable career fields. It does not copy identities, documents, qualifications, scores or event history.

Existing active, resolved ParticipantIdentityLink rows are a **read projection**: source records remain intact and contribute to their reviewed candidate/root in the staff directory and domain counts. Only person-to-person links are eligible. Conflicting source candidates and cycles remain separate. Reversal is reflected on the next request; there is no persistent derived identity store. Existing match/contact/reconciliation write rules are unchanged. A redirected alias cannot be edited through the new staff forms. Source document/qualification records still retain their original participant and version relationships.

New participant-owned extensions:
- ParticipantAcquisition: one original Event, Online or explicit unknown source; optional original date, protected event/submission reference, evidence provenance, attributed corrections. No inferred historical backfill and no automatic intake/profile mapping.
- ParticipantWorkExperience: attributable structured staff-entered work history, source reference and soft archive. The reported overall years field is independent of overlapping job durations.
- ParticipantNote: general staff-only notes, immutable original author, attributed edits, idempotent creation key, soft archive, and core AuditLog history. Existing ParticipantReviewRecord remains an independent append-only review decision.
- SavedParticipantView: account-owned versioned JSON criteria with unique retained names; soft archive and permission checks on every use.

All references use protected existing records. Migration 0015 is additive; no data migration, guessed provenance or score recomputation.

## Services and presentation

workspace_access uses existing active Django staff accounts and permissions. workspace_services validates reasons, locks participants/records, checks revision tokens and writes existing core AuditLog. workspace_forms allowlists editable fields; public participant forms remain unchanged. workspace_views composes the existing domain evidence into staff-only pages. No public participant response is expanded.

discovery supplies one validated, permission-checked population to the directory, saved views and participant intelligence. It uses Exists/subqueries and distinct canonical person IDs rather than joined row counts. Evidence-status filters reuse the existing effective verification service, including stale snapshots and expired/replaced evidence. Charts use the same discovery criteria as their destination lists. Evidence filters stream in bounded prefetch batches but still inspect the relevant evidence population; benchmark large deployed datasets before promising a latency SLA.

event_reports/activity.py factors the pre-existing accepted-source queries out of performance(), preserving event-reporting semantics. New participant metrics use those querysets plus coherent completed official game sessions and stored raw scores. No assessment runtime/scoring code changes.

The UI extends operations/design_base.html and reuses the actual Event Workspace cards, page header, actions, status language and responsive layout. Only participant-scoped CSS is added. Private documents continue to download through the existing permission-checked attachment response; no new public media URL or inline execution surface.

## Permissions

Existing view_participant plus active is_staff is required for the new workspace. Each document, qualification, event registration, quiz, simulator, game, historical result, approval and review keeps its existing domain permission. New:
- change_participant: audited profile/work-history management.
- manage_participant_acquisition: original-source correction, independently of general profile editing.
- view_participant_analytics: participant intelligence in addition to underlying domain permissions.
- view_participant_activity: factual staff-change timeline; private note changes still also require note-view permission.
- view/add/change_participantnote: private note access/create/edit. Staff may edit only their own notes; superusers may edit with attribution.
- Saved views are always owned by the requesting account, regardless of broad Django model permissions.

No automatic role/permission grants, authentication changes or operational user changes are included. Existing download/audit and approval permissions are retained.

## Future customer notes: architecture only

No authoritative customer organization/membership model exists at baseline. Build 11 does not introduce a placeholder organization, free-text tenant identity or a parallel customer-notes table. ParticipantNote can represent only btw_internal; a database check and server-side scope rejection deny customer authorship now. can_create_customer_note returns false even for superusers.

Build 13 must establish authoritative organization and employee membership records. Build 14 may extend the same participant-note foundation with:
1. A required protected organization FK for customer-audience rows, backed by a database audience/ownership constraint.
2. Author membership in that exact organization at creation; BTW staff must never impersonate a customer author.
3. Durable organization ownership and protected/attributed author history after membership ends.
4. Central tenant-scoped queryset/services for list/detail/edit/history, with forged object/organization ID tests.
5. Authorized BTW read-only access through a separate read policy; no staff customer-note creation.
6. No participant-portal exposure, no other-customer access, and explicit ownership on shared displays.

These are future requirements, **not implemented customer-note functionality**. Build 11 has no customer-facing note endpoint.

## Portal / CRM / reporting boundaries

Build 12 should reuse the same Participant and career/work/document foundations through narrowly allowlisted participant forms. It must preserve source attribution and distinguish claims from BTW verification. Staff notes, customer notes and internal evaluations are excluded by default. Existing limited account/profile workflows do not equal a completed Build 12 portal.

Build 15 matching must define approved suitability rules independently of profile coverage. Build 16 should reuse TaskParticipantLink, permission-filtered tasks and factual activity; there is no separate participant CRM, recruiting pipeline or communications automation here. Build 17 retains broader platform reporting. Tracker/Base44 data migration and physical hardware changes are outside scope.

## Intake follow-up

See [BUILD_11_INTAKE_AND_MAPPING_AUDIT.md](BUILD_11_INTAKE_AND_MAPPING_AUDIT.md). Existing intake, question publishing, answer-to-profile assignments, qualification mapping signals and offline protocols remain unchanged. Staff source tracking works; automatic source capture/transport, additional profile mappings and historical replay require owner review. Existing unknown data is deliberately not relabeled as known.
