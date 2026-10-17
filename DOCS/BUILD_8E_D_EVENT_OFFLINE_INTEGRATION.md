# Build 8E-D — Event & Offline Integration

## Baseline and scope

Built from verified Render preview commit 15cf2a62bfc161c9f92cf83e284880721db70ffb in the isolated build-8e-d-event-offline-integration branch. Reuses existing Event, Participant, EventRegistration, QR ticket, ExperienceSession/Activity, assessment, GameSession/Duck Hunt, SimulatorCapture and offline preparation/download/outbox systems. No schema or Render configuration changes. No attendance/check-in implementation, new notification system, full offline server, or dashboard redesign.

## Identity, progression and result authority

- Participant.identity_uuid persists across events. Event registration UUID and its unique active event/participant relationship identify participation; the experience UUID belongs to that registration. Capture submission UUID plus immutable fingerprint identifies an ingestion operation.
- Official QR scans validate current ticket, expiry/revocation, event, active registration, participant and station context. Browser kiosk grants bind the current ticket. Scans/session changes lock existing rows; repeat scans reuse the same experience.
- Configured official sequence is registration → Kiosk → Game → Simulator → Complete. Existing official no-game plans remain Kiosk → Simulator; adding a Game later does not silently alter an in-progress plan. Legacy Duck plans require their actual adapter or an audited authorized staff exception. Configuration changes that conflict with an existing game run require staff review.
- Quiz results use the existing snapshot/response/finalization service. Official mode must match the experience; later sections cannot accept answers before prior sections finish. Duplicate answers cannot replace accepted answers.
- Duck Hunt uses the existing server-controlled round, shot validation and result adapter. Matching result retries are safe; conflicting completed results are rejected. No placeholder adapter is claimed to be a working external Game integration.
- Simulator captures use the existing authenticated endpoint and reconciliation workflow. Event, station/profile, identity, prior steps and timestamps are validated; unresolved captures remain needs_review. Matching submission replays reuse the result; conflicting content cannot overwrite accepted results. Hardware/OCR transport and physical calibration are not certified by synthetic verification.
- Official station operators cannot mark completion through the generic operator shortcut. Actual result integrations advance completion. Trusted internal completion helpers remain available to adapters; they are not exposed as an unauthenticated completion API. Authorized skips require change_experienceactivity and preserve actor/reason audit data; completed sessions with skips are shown separately from scores.

## Offline boundary and recovery

Existing registration preparation, ticket allocation, central registration download, registration envelopes/outbox, retries and recovery screens remain in use. Offline registration reuses participant identity; duplicate event registration rolls back rather than consuming another ticket/envelope. An outbox operation key cannot be reused with conflicting preparation/type/payload; accepted operations are not reset or rewritten.

There is no new disconnected Kiosk/Game/Simulator result queue or full result-sync engine in this build. Registration readiness does not certify disconnected assessment, game, capture or trailer operation. Event UI shows only persisted preparation/outbox/download metadata and configuration checks; it does not infer Wi-Fi, Starlink, live connectivity or real-time device status. Rejected QR scans are not retained as a new historical event counter. Needs-review captures use existing authorized reconciliation, not automatic acceptance. Physical offline recovery/reconnection drills remain required before operational certification.

## Event operations and permissions

Event Detail presents distinct participant, session and accepted-result counts; available scores keep their original units and exclude void/replaced/test results. Registration, experience and exception data require existing model permissions; offline metadata has additional offline view permissions. A metadata-only event viewer cannot see participant records or register someone. Registration writes and new participant creation use separate existing permissions. Station activation and capture reconciliation enforce their existing model change permissions. Station scans expose minimal experience data and do not expose qualifications, documents, internal review notes or approval history.

Event Detail CSS is narrowly scoped to .event-operations and reuses the shared BTW foundation. Other portal stylesheets, dashboard, participant pages, public registration, kiosk and simulator design are unchanged.

## Verification

- Unchanged baseline full suite: 350 tests, OK, one skip.
- Final full suite: 379 tests, OK, one skip; 29 new integration tests.
- Focused integration/game runs pass. Django check, migration drift check and git diff --check pass. No migrations generated.
- The previously observed partial-label test selection reproduces 14 baseline database-table setup errors (234 tests); the normal full default suite passes both before and after. This pre-existing test-selection limitation was not hidden or expanded into an unrelated harness rewrite.
- Synthetic browser review uses an isolated temporary SQLite database with clearly labeled fictional event/participants; desktop 1440px and mobile 390px layouts checked, including actual completed/incomplete state and contained responsive tables. No real participant documents or credentials are used as test data.

## Deployment acceptance

Publish only by normal fast-forward-safe push to render-preview after checking the remote has not advanced incompatibly. Verify Render source commit, Live state, health endpoint, Home/Settings, Events, Participants and retained qualifications/approval routes. No main merge, production deployment, forced update or configuration change is authorized by this build.
