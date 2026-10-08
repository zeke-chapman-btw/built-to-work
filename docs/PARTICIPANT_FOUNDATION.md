# Build 7A: permanent Participant foundation

## Person, Event, and result ownership


django model | Meaning | Scope
--- | --- | ---
Participant | One durable person, with stable identity_uuid and legacy integer PK | Across Events
ParticipantCareerProfile | Optional career information for that person | Across Events
EventRegistration | A person's participation in one Event | One Event
QrTicket | A current or historical QR credential for a registration | One registration/Event
Attendance | The Event check-in | One Event
ExperienceSession | Official Event progression, or a separate nonofficial test/demo session | One Event
QuizAttempt, GameSession, SimulatorCapture | Scored activity records | One Event experience

A Participant can exist with zero Event registrations or experiences. General website, direct-link, or staff-created people can be recorded first, then matched to the same permanent identity when they later attend an Event. The registration foreign key points to Participant; Participant creation does not require an Event.

The existing integer Participant PK and foreign keys remain unchanged. Build 7A adds identity_uuid as a durable, unique UUID for future APIs and synchronization. The migration gives each existing row its own UUID before enforcing uniqueness. A changed phone or email does not change either ID or any historical Event relationship.

Current registration input stores no general address or career answers. Participant therefore gains only city/state/postal_code; optional career fields live in a one-to-one ParticipantCareerProfile. Event-specific intake responses, campaign source, and consent snapshots should be modeled with registration-scoped records when those workflows are built. Do not copy Quiz/Game/Simulator scores into permanent Participant fields. Registration contact snapshots may later be warranted for audit, but none are added here.

## Future Event/Campaign protection and acquisition provenance

One permanent Participant may have general BTW profile data and multiple separate Event or Campaign relationships. Future protected-campaign policy belongs on those relationships and their data, not on a second Participant identity. For a pre-existing BTW person, earlier general profile information can remain general while new protected Event/Campaign records are withheld. For a person first acquired through a protected campaign, a future visibility rule may delay general-pool availability until the configured protection period ends. Registration, attendance, or experience completion may start that period depending on the later Event/Campaign policy. Duration (for example 90 days), access grants, and expiration must be modeled and enforced in a later build; no current 7A eligibility query grants customer access or evaluates protection.

Permanent Participant acquisition provenance is distinct from EventRegistration.source, which records how one particular Event registration happened. Build 7A does not add a single mutable acquisition_source string: it would lose the original source, optional Event/Campaign link, timestamp, and protection context that matter for first acquisition. Build 7C should add an immutable acquisition/provenance record with those relationships and then derive the first-source summary if the product needs it. Until then, creation services do not claim to record a verified acquisition source.

## Identity matching and profile updates

apps.participants.identity normalizes email with strip + casefold and phone to digits, matching the existing stored normalization. It accepts a stable Participant identity_uuid when available. Otherwise it searches active real-person records by normalized email or phone. Zero candidates creates a new Participant, one reuses it, and multiple return an explicit ambiguous outcome with candidate IDs for staff review. A supplied unknown/invalid UUID never silently creates a person. Name alone never auto-matches. A matching contact does not silently overwrite the matched person's profile. Shared or changed contact details are allowed; there is no naive unique-person constraint on phone/email. Concurrent intake for an unmatched contact still needs serialized handling or later duplicate review; this service does not claim a global identity lock.

The staff registration workflow now calls the same matching service. The existing participant account request uses its candidate query. Future intake can use the service without copying its match rules. update_participant_profile validates and normalizes approved permanent fields under a transaction, updates the optional career record, and leaves Event registrations and results alone. A future Participant Portal should authorize the caller's ParticipantAccount before calling it.

## Permanent BTW test identity

Participant.kind distinguishes real people from the single system_test identity. The identity is created lazily by get_or_create_test_participant and receives a random, reusable test_qr_token; the token is not hard-coded. Provision it with python manage.py provision_test_participant in the isolated application checkout. Use --show-token only in a trusted terminal when configuring a staff-held QR; the token is generated in the database, never stored in source. The Django Admin marks this record as system_test and exposes its identity fields only to authorized Admin users. Its reserved simulator identifier comes from settings.SIMULATOR_TEST_IDENTIFIER (currently 0000000000), not its contact_phone. Never treat this identifier as a real phone number.

A kiosk scan of that token starts or resumes an isolated staff_test ExperienceSession for the active, open Event. The normal Event window and station assignment rules still apply; the reusable credential does not grant an Event-window bypass. TestParticipantRun links the permanent test identity to that nonofficial experience while the experience's official participant and registration fields remain null. There is no EventRegistration, QrTicket, or Attendance created by a test QR. The regular assessment and game UIs use their existing staff_test mode. The service-menu Test Mode without a permanent identity continues to work.

reset_test_participant_run checks the run and refuses any official result, marks only its nonofficial quiz/game results void, timestamps the old run, and starts a fresh staff_test experience. Historical rows remain for diagnostics. A completed test run is automatically reset on its next QR scan; a run in progress is resumed. The current Build 6A simulator capture path recognizes 0000000000 first as a nonofficial test capture and remains unchanged. When an active test run exists for the same Event, a reserved simulator capture links to that test Participant and staff_test ExperienceSession; if earlier activities are complete, it completes only that nonofficial Simulator activity. Without an active test run, the Build 6A test-capture behavior remains unchanged. Reset voids linked test captures while retaining them for diagnostics.

## Official eligibility and sharing boundary

apps.participants.eligibility provides reusable querysets for official people, registrations, experiences, quiz attempts, game sessions, and simulator captures. They require real-person kind plus each domain's official/accepted flags. They are eligibility filters, **not** customer authorization. Every future customer query must also apply explicit campaign/Event grants and a permitted field projection. Internal notes, contact details, candidate-shareable profile fields, sensitive information, and Event results are separate disclosure categories. No Participant field is implicitly customer-visible.

The official registration/ticket/check-in services reject the system test identity. Official assessment creation rejects a nonofficial experience. Existing explicitly nonofficial review attempts on official sessions remain supported. Simulator phone matching excludes the test identity, while 0000000000 stays nonofficial by the existing reserved-identifier branch. Normal participants retain the existing one-active-registration and one-current-official-attempt constraints.

## Future boundaries

BTW remains authoritative for Participant identity, Event registrations, experience state, results, and candidate operations. Future HubSpot synchronization may receive selected profile/intake fields for CRM communication, marketing, follow-up, and campaign workflows; it must not become the system of record or automatically mirror every score. No HubSpot sync is part of 7A.

Participant Portal login/UI, public intake, consent history, resumes/documents, customer grants, duplicate merge UI, content authoring, test-QR Admin screens, and broader Admin redesign belong to later passes. The existing ParticipantAccount foundation is preserved. Build 6B Windows helper work in the original checkout is outside this branch.
