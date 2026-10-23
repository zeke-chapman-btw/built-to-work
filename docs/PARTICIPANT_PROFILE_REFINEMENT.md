# Participant profile refinement and assessment visibility

This focused pass starts at 5cfa68b154d7e59b81aab898d56d31939681c1bf.
The actual Base44 /staging/participant-portal reference was inspected on desktop
and in its phone preview before implementation. Its wide section panels, compact
identity summary, skill chips, work timeline and document cards inform the profile.
BTW's portal shell, fonts, navigation, section editors and security stay in use.
The prototype's completion score, employer-visibility claim and unrestricted
removal controls are not copied.

## Owner-approved result policy — 2026-10-23

The owner explicitly selected automatic display of finalized official quizzes
and accepted official simulator scores. Kiosk quiz results already have a
participant-facing presentation; simulator participant visibility is a newly
approved policy, not an existing per-record release flag.

- Reuse apps.event_reports.activity.activity_sources. Its official-mode and
  participant/event/registration/session consistency checks remain authoritative.
- Completed non-void QuizAttempt records must have both completed category
  sections with recorded snapshots. Use existing correct_count/total_correct and
  frozen category labels. Do not expose questions, choices or answer keys.
- SimulatorCapture must be official, matched or resolved, non-void, correctly
  attached to the accepted session/registration/person/event, and have a recorded
  percentage in the established 0–100 range. Display total_score as stored;
  never recompute hardware results or expose diagnostics/reconciliation reasons.
- Resolve the signed-in identity through own_account and identity_members. Reviewed
  same-person links preserve historical ownership; contact similarity is not a link.
- Staff notes, internal evaluations, unresolved/voided records, demo/staff-test
  activity and imported HistoricalSimulatorScore rows are not released here.
- The template receives only an allowlisted result type, stored score/count,
  category names/counts, real date/timezone and event name. It receives no staff
  evaluation data. Dates are displayed in the event's recorded timezone.

The projection reads existing records each request. Separate eligible results
remain in descending date order and are paginated ten per page. New eligible
results appear automatically. Voided or replaced results remain stored as audit
history but cease to be current participant-visible results. No participant
assessment-write endpoint, new scoring engine, model or migration is introduced.

## Profile and preview boundaries

The existing personal/career/work/qualification editors retain revision checks,
request replay protection, audits, ownership rules and verified-evidence locks.
The upload page now uses the profile shell with Back/Cancel and the same existing
multipart form, storage and document services. No authentication or storage
infrastructure changes.

Admin Portal Preview is reused. Synthetic assessment demonstrations must use its
existing participant and private event, never operational participant assessments.
Creating or viewing demo results must not issue tickets or consume allocation
ranges, send invitations, or change the administrator's account. The read service
also pins a preview context to that fixture's private event and participant.

Production, staff-access development, operational readiness, Customer Portal,
job publishing/matching, intake design, kiosk screens and simulator ingestion
remain outside this pass.
