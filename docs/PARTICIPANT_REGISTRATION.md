# Participant registration foundation

The public registration flow lives at /participants/register/ and persists a permanent Participant, career profile, versioned RegistrationSubmission, and consent acceptance when an approved document exists. Submissions preserve the form-version snapshot used at submission time.

Administrators manage the standard intake question set at /participants/staff/registration-forms/. Editing and publishing require a superuser; published versions are immutable snapshots. Protected identity fields allow copy edits while preventing structural changes.

The registration form accepts an optional event context for future event-aware registration. Event registrations, tickets, and QR issuance remain outside this phase.

Image/file decoding hardening and resume/document uploads are intentionally deferred until the upload policy and storage requirements are approved.
