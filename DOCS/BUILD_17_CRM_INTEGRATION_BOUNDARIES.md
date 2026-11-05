# Build 17 integration boundaries

This records the Build 16 extension points. It does not authorize or implement Build 17.

## Existing authoritative records

Participant identity, Customer organizations/contacts/memberships, approved Workforce Needs, Workforce Requests, Campaigns, recruiting opportunities/candidates and exact candidate disclosures remain authoritative. CRM links these records instead of copying their identities, permissions or business state.

`crm.Interaction` has a stable UUID, immutable BTW/employer audience, original author and associations. An occurrence timestamp can be backdated; `created_at` and `InteractionRevision.recorded_at` describe when BTW actually recorded the information. Future consumers must distinguish these clocks.

## Explicit event boundaries

| Current service operation | Retained structured evidence | Future integration point |
| --- | --- | --- |
| `create_internal` / `create_employer` | Audience, type, associations, occurrence/creation timestamps, operation ID, payload fingerprint; revision `created` | A separately approved consumer could react after commit, using the current visibility/disclosure policy |
| `correct` | Optimistic revision, actor, reason, immutable earlier snapshots; revision `corrected` | Compare relevant structured fields; do not infer events by scraping screen copy |
| `archive` / restore | Actor, reason, archive status; attributed `archived` / `restored` revisions | Cancel or suppress future work according to an approved retention/reminder policy |
| Explicit manual Task create/link | Existing Task and its ordinary history/notifications, typed interaction link; revision `task_linked` | Reuse existing Task permissions and deduplicate any later approved automatic creation |
| Contact relationship save | Existing contact ID, role, method preferences, actor/revision; existing native primary-contact service and explicit audit | Recheck preferences and applicable consent/suppression before any future provider operation |

No consumer, event bus, CRM rule builder, scheduled reminder, notification generator or automatic Task creation exists in Build 16. A manually entered follow-up date is planning information, not a scheduled job. Explicit manual Task creation retains the existing Task engine's ordinary notification behavior.

## Recommended future safeguards, not implemented

Future work should use a post-commit/outbox boundary if reliable asynchronous delivery is approved, with an idempotency key such as interaction UUID + revision + consumer/action. Retries must recheck current authorization, company membership and exact candidate disclosure rather than trust an old snapshot. Revocation cannot be bypassed through queued work.

Employer interactions must remain a separate ownership/query/UI family. They cannot become BTW-authored touchpoints, participant-visible notes, official hiring decisions, additional candidate releases or internal Tasks as an automatic side effect. Archived opportunities remain subject to the existing recruiting access policy; retaining a record does not grant broader historical access.

Provider references and delivery-status fields reserve a future association point; Build 16 has no provider writer, mailbox reader, EZ Texting API client, webhook, outbound queue or delivery confirmation. A later provider integration needs explicit authenticated provenance, duplicate receipt handling, status-transition rules and communication eligibility checks. Opening an external Gmail compose URL alone must never create a sent-message record.

Contact method preferences are not consent. Candidate disclosure is not blanket marketing permission. No unknown preference or historical record should be promoted into affirmative permission. Mass marketing and HubSpot remain outside Build 16.

CRM has no newly implemented trailer-to-cloud synchronization path. Existing synchronization capabilities must not be presented as central CRM or assessment-result synchronization. Any future offline CRM synchronization requires separately reviewed identity, ownership, conflict, revocation and idempotency contracts.
