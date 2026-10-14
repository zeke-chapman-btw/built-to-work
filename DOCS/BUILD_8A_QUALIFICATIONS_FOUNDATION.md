# Build 8A  Participant Qualifications Foundation

## Scope

Build 8A adds a canonical qualification catalog and participant qualification records. It provides explicit mappings from published registration-form questions to catalog entries, plus authorized administration of the catalog, mappings, and participant records. It does not implement public profiles, credential verification, employer search, qualification expiration workflows, or later Build 8 features.

## Data model

- QualificationCategory is the editable, ordered grouping for catalog entries.
- QualificationCatalogEntry is a stable, uniquely identified qualification value (skill, certification, license, qualification, or experience).
- RegistrationQualificationMapping maps one exact published question/answer on a registration form to a catalog entry, or explicitly maps a text question to a custom qualification name.
- ParticipantQualification links a participant to a catalog entry or custom name and retains experience details, source, source submission/question, answer and mapping snapshots, and whether the record is self-reported.

The schema is additive. Existing participant, registration, consent, ticket, and synchronization data is not rewritten. Catalog entries are deactivated rather than deleted so existing qualification history remains interpretable.

## Mapping behavior

Only active, published mappings attached to a submitted registration form are considered. Choice mappings require an exact configured answer. Free text is never inferred into a catalog entry; a text answer is captured only when an administrator explicitly configures a custom-text mapping. The submission processor is repeat-safe and records the source answer and mapping configuration as snapshots. Editing or retiring a mapping later does not retroactively change participant records already created.

Qualification data originating from participant registration is marked self-reported. It is not verified proof of a license, certification, or skill. Staff-entered changes are distinguished by their source and audited through the existing audit log.

## Administration and permissions

In Django Admin, use the dedicated permissions on qualification categories/catalog entries, registration mappings, and participant qualification records. Catalog/mapping configuration permissions are separate from participant-record management permissions. Regular staff without those permissions cannot read or change the protected admin pages. The participant record is available from the existing Participant admin page; provenance and source snapshots are read-only. Admin changes are written to the BTW audit log. Existing participant access policy still applies; do not expose these records through public endpoints.

## Registration integration

The shared registration submission model covers online and trailer-local registration flows. The qualification signal processes only the transition into submitted state (or a newly created submitted record), and the service independently checks submission status, participant linkage, question/form eligibility, and explicit published mapping configuration. Registration without mapped questions continues to work normally.

## Synchronization and later work

Build 8A does not change the Build 7 trailer synchronization protocol or payload. Qualification records derived from offline registrations are currently local to the submitting instance unless the existing submission-transfer behavior carries the source submission and it is reprocessed there. A versioned, explicit synchronization contract for canonical qualification records and catalog/mapping versions is a later integration dependency (planned for Build 12). Do not assume that catalog configuration or every canonical qualification record is centrally synchronized by this foundation.

## Validation

Run the focused tests with: python manage.py test apps.participants.test_qualifications_8a -v 2. Run the repository's normal Django suite and the established Build 7 virtual synchronization runners before checkpointing. Apply the normal migration checks; migration 0010_qualifications_foundation is additive.

## Limitations

- No external credential/license verification is performed.
- A self-reported answer must not be presented as verified.
- Historical and non-registration qualification imports are outside this build.
- The mapping form supports only the explicit mapping types and exact-answer behavior implemented in the model/service; it does not perform fuzzy matching or infer skills from arbitrary prose.
- The separate Build 6B checkout and simulator integration are not changed by Build 8A.


## Staff-facing qualification records

Authorized staff manage participant records in the BTW application at /participants/staff/qualifications/. The page supports participant search, qualification review, add, and update. Access requires staff status plus participant and qualification view permissions; create and update also require the corresponding qualification model permission. Each save requires an audit reason and records before/after data. The form exposes active qualification choices and participant-record fields only; catalog and registration-answer mapping configuration remain restricted to the BTW Admin interface. Registration-derived answer and mapping snapshots are excluded from editable fields and are retained when staff update a record.

