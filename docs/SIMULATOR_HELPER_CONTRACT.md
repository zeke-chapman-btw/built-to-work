# Simulator capture helper contract (Build 6A)

This document defines the server contract for a future Windows SimU capture helper. Build 6A does not install or run that helper. The Django API is the receiving side. SimU remains the operator-facing simulator; its screen is observed, not modified.

## Observed hardware and calibration

The reviewed trailer uses Windows at 100% display scaling with NVIDIA Surround at 3330 x 1920 and a portrait center display. The observed SimU flow shows a Username entry and Submit, followed by an upper-right TOTAL SCORE. Only TOTAL SCORE is captured. These observations are a starting point, not universal coordinates or assumptions for other trailers.

Each active SimulatorCaptureProfile records a simulator type, version, optional expected display dimensions, and calibrated identifier, score, and state regions as x/y/width/height pixel rectangles. A future helper must verify the active display geometry and use the profile selected for its Event and Station. Recalibrate for any changed display topology, scaling, SimU layout, or capture surface. The helper must not submit a guessed region.

## Helper state machine

WAITING FOR ID -> ID CAPTURED -> WAITING FOR SCORE -> SCORE CAPTURED -> SUBMIT RESULT -> RESET -> WAITING FOR ID.

The helper should capture the identifier entered in SimU, wait for a stable TOTAL SCORE result, and submit one durable observation. It should generate and persist a UUID submission_id before its first network attempt. On a network failure it retries the same body and UUID, rather than inventing a new result. It should not infer a score from intermediate screens. A reset clears captured display state for the next simulator user.

## Local API

POST /api/simulator/captures/ with Content-Type: application/json and Authorization: Bearer <SIMULATOR_INGESTION_TOKEN>. Configure the token only in the server environment. The endpoint is disabled when that setting is empty. The helper must use the trailer-local Django endpoint when offline; cloud availability must not be required. Protect this bearer token and the local transport before deploying on a shared LAN.

Required JSON fields: submission_id (UUID), event_id (UUID), station_code, profile_id (UUID), profile_version (string matching the selected profile version), raw_identifier, raw_total_score, captured_at (timezone-aware ISO 8601). Optional diagnostic_metadata is a small JSON object for non-sensitive capture diagnostics. Never include bearer tokens, screenshots, participant names, or unrelated PII in diagnostics.

The server returns capture_id, outcome, accepted, activity_completed, and idempotent_replay. A new accepted capture returns HTTP 201; exact retries return the stored result with HTTP 200 and idempotent_replay=true. Reusing a submission_id with different content returns 409. Authentication and validation failures return 4xx; disabled ingestion returns 503. A 2xx review outcome means the observation was durably stored, not that official progression was accepted. The helper must retain its local delivery record until a 2xx response.

The configured profile is versioned. The API stores both profile version and a configuration snapshot with each observation so later recalibration does not rewrite history. Captured_at is supplied by the helper; received_at is server time. The server stores the raw identifier and raw score for audit, but does not echo them in API responses.

## Matching and isolation

The Event and Station must have an active EventSimulatorConfiguration and the Station must be assigned to that Event. The server strips configured prefixes such as tel and normalizes the identifier using the existing participant phone normalization. Matching is scoped to the submitted Event's active registrations; no participant-global lookup is used to accept a result. Exactly one matching registration and official ExperienceSession must exist. Quiz and configured Game prerequisites must already be complete.

The reserved SIMULATOR_TEST_IDENTIFIER (default 0000000000) always yields a nonofficial test capture. Unknown, ambiguous, malformed, stale-profile, wrong-Event, out-of-window, incomplete-prerequisite, and duplicate-official observations remain stored for staff review. They do not complete a Simulator activity or alter an official result. The API never creates a Participant, EventRegistration, Attendance, QR ticket, or ExperienceSession.

Only accepted official captures with status matched or resolved, is_official=true, and voided_at=null are eligible for future official reporting or leaderboards. staff_test, demo, test-identifier, unresolved, and voided captures must be excluded. A second official capture is retained for review. Staff reconciliation requires an authorized Django staff user, a reason, the same Event's registration and official session, and an explicit replacement choice when an accepted result already exists. Replacement retains the earlier capture as voided history; it does not delete evidence.

## Operational boundaries

Set SIMULATOR_INGESTION_TOKEN and, if needed, SIMULATOR_TEST_IDENTIFIER in environment configuration. Keep the token out of source control and logs. Restrict the endpoint to the intended trailer network and protect the transport before field deployment. The future helper should log only non-sensitive state transitions and delivery IDs. Secure staff review UI, Windows service installation, OCR implementation, camera/image capture, and cloud synchronization are outside Build 6A.

## Build 6B local XML monitor boundary

The Windows Simulator Results monitor is documented in [SIMULATOR_WINDOWS_HELPER.md](SIMULATOR_WINDOWS_HELPER.md). It collects and archives SimU XML locally; this Build 6B branch does not connect that local index to this ingestion API. The official SimU scoring formula and server synchronization remain pending. The earlier server-side calibration import command and its test were not available in this Codespace and are not part of this commit; recover or verify them separately.
