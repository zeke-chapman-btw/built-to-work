# Build 8E-C — Qualifications, Documents & Internal Review

Baseline: `dcb1013cd4baac8bc055c9406a0300ada18f355c` on Render preview. Includes the four preserved Home Dashboard surface-refinement files.

## Workflow

- Existing ParticipantQualification records remain the source of equipment/trade experience, years, CDL classes/endorsements, licenses, certifications and training. Optional issue/expiration dates extend those records; catalog or descriptive custom entries remain supported.
- Existing append-only qualification/document verification records provide Unverified, Pending Review, Verified and Rejected decisions. Reviewers and review times come from the authenticated request. Self-reported provenance remains visible and is never automatically verified. Material qualification changes invalidate earlier snapshots; expired qualifications/evidence cannot be newly verified.
- New append-only ParticipantReviewRecord is a separate BTW internal review with Pending Review, Approved or Rejected, mandatory internal notes, reviewer/time, superseded history, idempotent requests and stale-review protection. No score, document upload, qualification verification, customer sharing or hiring automation changes participant approval.
- Profile links connect qualifications, documents, verification history and the separate participant review. Existing registration, quiz, game and historical simulator sections are retained. Scoped workflow CSS uses the shared BTW shell, fonts, cards and form components.

## Permissions

Active staff and existing participant/qualification/document permissions remain required. Internal review additionally requires `participants.view_participant` plus `participants.view_participantreviewrecord` (read history) or `participants.review_participants` (record decisions). Qualification reviewers do not automatically receive internal participant review authority. No existing group is expanded by these migrations. Document audit history is separately permission-gated; all downloads pass through the authorized staff endpoint.

## Documents and storage

Reuses ParticipantDocument, categories, version replacement/archive history, audit records, evidence links, PDF/PNG/JPEG signature checks and the 20 MiB upload limit. Categories are seeded additively without replacing existing categories. Same-participant evidence linking is enforced. Files have randomized internal names, 0600 permissions and no public storage URL.

The existing preview PRIVATE_DOCUMENT_ROOT was verified as `/var/data/participant_documents`, outside public MEDIA_ROOT/STATIC_ROOT on the existing persistent `/var/data` Render disk. A labeled synthetic write/read probe passed and was removed. Render configuration is unchanged. The workflow depends on Django storage operations rather than public file paths; a secure permanent backend can be integrated centrally later. This is preview verification, not a production storage/backup/security certification; malware scanning and production retention/storage policy remain future infrastructure work. Never configure permanent uploads on Render temporary storage.

All development fixtures and uploaded verification documents are fictional and explicitly labeled SYNTHETIC. No real license, certification, resume or participant evidence was used. The Chrome extension blocked direct browser file upload because file URL access is disabled; no extension permissions were changed. The actual upload endpoint was tested with Django Client, and the resulting page and secure browser download were verified.

## Migrations and validation

0013 adds nullable qualification dates and the independent participant review table/permission; 0014 additively seeds document categories. An isolated database upgraded from participants 0012 preserved a synthetic existing qualification and its experience/provenance; dates stayed null and no verification or approval was fabricated.

102 focused qualification/document/security/profile/dashboard/navigation/Events tests pass, including 13 new integration tests. Django check, makemigrations --check --dry-run and git diff --check pass. The broader 233-test run has 14 errors in offline downsync/backup tests using secondary databases (missing consentdocumentversion/trailerinstance tables), plus one skip. The unchanged baseline reproduces exactly those same 14 errors in its 221-test run. These unrelated baseline failures were not modified in this build.

Desktop (1440px) and mobile (390px) browser review passed for profile, qualifications, private documents and separate participant approval. No horizontal overflow was observed; the Settings gear and mobile navigation work. The document metadata audit now captures the original values before form validation mutates the instance.
