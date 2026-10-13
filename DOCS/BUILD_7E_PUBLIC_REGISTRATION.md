# Build 7E  Public event pre-registration

## Staff setup

In the authorized Event detail page, choose **Manage public registration**. Select the approved consent document for that Event, set a unique slug (or leave blank to derive one from the Event name), and open registration. The Event must be upcoming, inside its registration window, and have a published standard intake form. Pause, close, or disable the link from the same screen. Old slugs redirect in one hop to the current slug; a slug cannot be claimed by a different Event. Staff can preview the form and download a QR code for the shareable URL. These controls require event-change permission and are audited. The local trailer registration route is unchanged.

## Participant journey

The public URL is /e/<slug>/. Participants see published Event details, the existing intake and active Event questions, active group choices (capacity and deadlines are enforced by the server), the Event's approved consent text, and unchecked email/SMS delivery choices. The server checks dates, capacities, required answers, consent, and current public status at submission. A request key makes browser resubmission idempotent. Successful submission creates a central Participant and stable EventRegistration, preserves the published intake form version and Event question/answer snapshot, records consent evidence, issues one cloud-range ten-digit ticket, and queues opted-in delivery through the durable communications system. The completion page links to the existing branded ticket and downloadable PNG. It does not check the participant in.

Existing identities are never exposed or automatically linked based only on user-entered contact information. A returning person may need authorized reconciliation of a second Participant record. This avoids unsafe account disclosure or automatic merges.

The production legal PDF and verified published text remain a deployment prerequisite. A missing, unapproved, or not-yet-effective Event document blocks public registration. The source code contains no substitute legal text; tests use synthetic consent text only.

## Build 7F handoff

Use the central Participant.identity_uuid, EventRegistration.id, Event.id, current QrTicket number/token/expiry, group ID, RegistrationSubmission.id and form_version, answer_snapshot (including Event questions), ConsentAcceptance document/version/hash/time, registration source, and created/updated timestamps. The public flow uses the cloud ticket allocation block, separate from trailer-reserved ranges. Build 7F must define a central-to-trailer transfer with idempotent acknowledgments, conflict review, consent/document compatibility, and ticket validity checks; no transfer engine is implemented here.

## Verification boundaries

Automated tests use SQLite and mocked delivery queues. PostgreSQL row-lock concurrency must be verified on PostgreSQL before launch. Real provider delivery, production legal PDF, externally accessible domain/HTTPS, physical printing, and trailer synchronization require deployment or later-build verification.
