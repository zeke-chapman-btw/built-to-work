# Participant profile self-service

The participant portal uses the canonical Participant, career profile, work-history,
qualification and private-document records. My Profile is a read-first page; its
section editors reuse the existing participant authentication and Admin Preview
boundary. It is not a separate questionnaire or profile database.

## Intake-to-profile map

| Existing input or data | Canonical storage | Participant experience |
| --- | --- | --- |
| Name, preferred name, contact email/phone, city/state/postal code | Participant | Independent personal-information editor; existing editable-field policy retained. DOB, identity UUID, classification and staff notes are not accepted. Preview contacts stay disabled. |
| Employer, role, employment description/duration; earlier jobs | ParticipantWorkExperience and immutable intake submissions | Readable individual jobs. Add makes another row; Edit updates only an eligible existing row. Original submission, source entry ID and duration remain retained. |
| Repeated hands-on areas, equipment, practiced skills, proficiency | ParticipantHandsOnExperience with frozen definition/submission | Historical intake disclosure; these questionnaire records are not rewritten. New work or skill entries can be added separately. |
| Existing skills summary | ParticipantCareerProfile.skills_interests | Summary retained and independently editable. New skills are additional qualification records, not text appended repeatedly to the summary. |
| Education, school, graduation, training summaries | ParticipantCareerProfile.education_training and retained intake data | Existing summary retained; independent summary editing when present. New education/training has its own repeatable qualification entry with provider and dates. |
| Licenses, certifications, mapped qualifications | ParticipantQualification and verification/document links | View evidence-aware status; edit only eligible self-reported unreviewed custom records. New credentials start unverified. |
| Industries, employment interests/status, work arrangement, preferred locations, overall years | ParticipantCareerProfile and IntakeFieldState | Independent current-preferences editor. Existing signals preserve provenance and protect current manual changes against later intake replay. Original answers are retained. |
| Career timeline/availability and travel answers | ParticipantCareerProfile and intake projection | Independent availability/travel editor. Historical intake responses remain separate. |
| Resume/certificate/license files | ParticipantDocument and existing private storage/audit | Existing upload, authorized retrieval and permitted archival; no new storage, public file route or top-level document navigation. |

## Minimal extension

Migration `0020_participant_profile_entries` adds three fields to the existing
ParticipantQualification model: `reported_kind` (skill/training/credential),
`reported_provider`, and nullable unique `portal_request_key`. Source choices now
identify participant-reported rows explicitly. No existing row is backfilled into
a new category or marked verified. Existing qualification catalog and intake
mapping semantics remain unchanged.

New skills, training and credentials therefore appear in the existing authorized
staff Qualifications workspace and existing qualification search. Provider and
entry kind are shown there. New work is in the existing Work history section.
Future recruiting can use these same participant foreign keys; matching or
ranking is outside this change.

Legacy verification snapshots retain their original shape. The new section and
provider fields enter verification snapshots only when present, so existing
independent decisions are not invalidated merely by this upgrade.

## Ownership and history

- Each request derives the participant from the authenticated account or validated
  synthetic Admin Preview context. URLs and posted participant IDs never select
  another identity for writing.
- Writes lock the canonical participant and target entry, check a revision for
  edits/archives, and use repeat-request identifiers for additions. PostgreSQL
  locks explicitly target the record rather than nullable catalog joins.
- Existing eligible intake work/custom claims can be clarified without changing
  their original submission or provenance. Unknown original creators stay unknown;
  the later participant editor is recorded separately.
- Staff-created, staff-edited, catalog-linked or reviewed qualification data is
  retained. Verification records, linked evidence and internal notes cannot be
  changed through the participant editors.
- Only participant-created unreviewed additions can be archived through these
  routes. Archiving retains the record and audit history. Original intake rows
  cannot be archived here.
- Existing document visibility and evidence-retention checks are unchanged.
- The old profile POST contract remains for compatibility, but the normal page
  renders saved summaries and links to independent editors.

## Navigation and scope

The approved Job Opportunities navigation refinement is included: a separate
participant header link and a clearly inactive coming-soon page. It implements
no job publishing or Customer Portal functionality. The existing portal shell,
Events, My Activity, Account, Preview banner and Return to Admin remain in use.

Operational-readiness recommendations (staff onboarding, broken default staff
login, SMTP, stable real-data hosting and recovery) are not implemented in this
focused profile build. Preview verification does not approve real-data rollout.
