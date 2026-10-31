# Trailer Control Center V1 — local-only candidate

Owner-approved October 31, 2026. Local implementation only; no commit, push or deployment is implied. The app is disabled unless explicitly enabled on the intended trailer-local installation. No existing role group memberships or permissions are automatically changed.

## Existing systems retained

Reuse TrailerInstance, OfflineEventPreparation, Station, participants/tickets/experiences, existing official SimulatorCapture acceptance and registration/intake sync. The separate protected Build 6B XML monitor is unchanged: XML delivery to Django and an approved XML scoring formula remain absent. Quiz/game/simulator results are not synchronized centrally by this module. Checklist completion is not equipment certification.

## Local configuration (names only)

TRAILER_CONTROL_ENABLED; BTW_TRAILER_IDENTITY; TRAILER_CONTROL_DATA_ROOT; TRAILER_CONTROL_VIDEO_ROOT; existing BTW_SYNC_ENDPOINT; BTW_CENTRAL_DOWNLOAD_URL; BTW_SYNC_API_TOKEN; BTW_CENTRAL_SYNC_URL; SECRET_KEY; ALLOWED_HOSTS; CSRF_TRUSTED_ORIGINS; existing database and private-media settings. Credentials must remain outside Git and outside screenshots/logs. Local HTTPS and trusted hostname are required for field installation. Do not run the physical trailer with DEBUG/runserver. No public inbound exposure is introduced.

Apply the additive trailer_control migration only after verifying the correct local database. Grant the narrowly scoped view/manage/sync/publication permissions only through an approved staff-access decision; Administrator retains superuser authority. Event Staff and Manager groups are not automatically broadened. A local staff login must already be provisioned; downloads do not transfer cloud sessions/passwords.

## Staff operation

Open `/internal/trailer-control/` directly or from Operations. Select an existing preparation for the configured trailer with a reason and revision check. This does not change station active contexts or event lifecycle. The dashboard links existing event/participant/checklist/assessment workflows only where their permissions allow. Registration/experience activity is not arrival attendance.

Supervise `python manage.py trailer_control_worker` locally. `--once` samples health and processes one bounded manual request. The page only queues a durable audited request. There is at most one pending/running request per trailer. Outbound batches are limited to 20 eligible items/45 seconds and preserve existing operation UUIDs, retry delays, receipt acceptance and review handling. Inbound delegates to existing pull_event/apply_download for the selected scoped catalog item, with five fetches/45 seconds maximum and force=False. Interrupted pages/cursors remain under the existing engine. Expired worker leases are recorded as failed, never as proof of successful sync; retry by an explicit new request. Existing automatic workers may be supervised independently using the established commands; no queue is replaced.

Sync counts partition Pending, Sending, Failed and Accepted. Last accepted time is delivery history, not connectivity. The known legacy OfflineRegistrationEnvelope acknowledgement metadata mismatch is unchanged; SyncOutboxItem accepted_at is authoritative for outbound delivery. Monitoring and worker endpoint configuration names differ; verify their intended HTTPS destinations. Redirects, credentials embedded in URLs and query-string secrets are rejected by the new manual orchestration.

## Devices and privacy

Administrator enrolls named devices and receives a random credential once; only its hash is retained. TV pairing uses a separate one-time five-minute code, capped attempts and an event/channel-scoped 12-hour display session. No Administrator account is used on the TV. Rotation/disable invalidates device access and paired displays. Do not photograph one-time codes or credentials.

Machine heartbeats: POST `/api/trailer-control/devices/<device UUID>/heartbeat/`, Bearer device credential, application/json, <=2048 bytes. Allowed fields: boot_id UUID; increasing sequence integer; selected event_id; state startup/idle/operating/paused/playing/error; optional finite playback position. No PII/extra fields. A 15-second interval is recommended, reports faster than five seconds rejected. Duplicate sequences and retired boots are rejected. Recent communication <=45 seconds, otherwise Stale; never seen is Unknown. These are communication thresholds, not power/readiness or Wi-Fi measurements. Device connections are declared. First-party display pages report their own state; no general kiosk browser or protected simulator agent is silently changed. Provisioning an external kiosk heartbeat requires its appropriate local adapter; until then show Unknown/last existing activity.

## Public displays

Explicit event-scoped publication is default withheld. Authorized staff chooses publication/withholding with a reason. Aliases are random event-specific strings, never derived from a name, phone, ticket or participant ID. Public projection includes only alias/rank/score, event and configured simulator/profile labels. Accepted official matched/resolved, current nonvoid results with consistent active registration/official experience and nonarchived person are eligible. Separate station/profile/version/immutable snapshot groups; shared ranks for ties. Preserve one current official capture per ExperienceSession. No normalization or alternative attempt-selection policy. Historical, test, demo, unresolved, voided and inactive-registration data are excluded.

Leaderboard refreshes locally every five seconds with bounded reconnect delay and visible last-success/stale text. Browser restart without local-server access shows unavailable rather than simulated scores. No participant data is stored in localStorage/service workers. A paired display has no participant search or staff navigation. Bindings are event-specific; changing dashboard context does not silently rebind TVs.

Video stays unconfigured until the existing approved asset is identified. The restricted local media route validates an approved basename, allowed root, SHA-256 and mp4/webm extension, rejects traversal/symlink escape and supports bounded byte ranges. Do not copy or replace the asset without separate approval. Muted autoplay/loop/playsinline and Play/Fullscreen fallback are implemented; actual TV codec support, startup and restart behavior need hardware confirmation. Playback heartbeat is not proof of physical screen visibility or remote TV control.

## Field verification still required

Confirm server OS/database/storage, stable local DNS/TLS, service supervision, edge backup/restore, actual TV/player models and approved video, station content/calibration and credentials. WAN loss must leave the local server/LAN available; complete kiosk catalogs/intake/consent/fonts/artwork/media must be preloaded. Event download does not install the full assessment/game/simulator catalog. Live screen streaming, router/AP telemetry, remote TV control, public exposure and future outbound central health telemetry remain outside V1. No physical hardware certification is claimed by local Codespaces tests.
