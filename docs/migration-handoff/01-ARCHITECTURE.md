# Architecture

Evidence: actual source at 01e48c2bc0adcac0a5047291791cdda40cbe5328 plus the repository documents cited below. Paths are repository-relative and case-sensitive. docs/ and DOCS/ are distinct directories on Linux.

## Stack and execution

- Python 3.14; .python-version requests 3.14.3. The reused Codespace virtual environment actually reports **3.14.2**, Django **5.2.18**. This discrepancy is documented, not changed.
- Django 5.2 LTS, server-rendered Django templates, CSS and browser JavaScript. No SPA framework or Node build dependency was found in the active checkout.
- SQLite defaults for local/trailer development; PostgreSQL supported by settings and used by the Render preview Blueprint. SQLite tests do not validate PostgreSQL locking.
- WSGI/ASGI entry points in config/. Render uses Gunicorn and WhiteNoise for collected static files. Timezone-aware storage with UTC default; event-local scheduling is explicit.
- requirements.txt declares ranges, not a frozen lockfile. Do not upgrade dependencies as part of a workspace move.

| Dependency declaration | Purpose |
| --- | --- |
| Django>=5.2,<5.3 | Framework, auth, ORM, templates, tests |
| psycopg[binary]>=3.2,<4 | PostgreSQL |
| python-dotenv>=1.0,<2; dj-database-url>=2.2,<3 | Environment/database configuration |
| qrcode>=7.4,<9; Pillow>=11,<13; CairoSVG>=2.7,<3 | Tickets, images and rendering |
| pypdf>=5,<7 | PDF processing |
| cryptography>=42 | Encrypted trailer backups |
| openpyxl>=3.1,<4 | Spreadsheet import/export |
| gunicorn>=23,<24; whitenoise>=6.8,<7 | Hosted serving/static assets |

## App ownership and key source paths

| App | Responsibility / important modules |
| --- | --- |
| apps.core | UUID/timestamp/archive primitives, AuditLog, explicit audit services, health, custom test discovery |
| apps.accounts | Custom UUID User; personal quick_action_ids JSON |
| apps.internal_ui | Shared staff portal, permission-filtered navigation, Home, personal Settings, Quick Actions |
| apps.events | Event lifecycle/logistics, groups/registrations/tickets, legacy Attendance, public/offline registration, sync/downsync, communications, incidents, backups |
| apps.staffing | StaffProfile, StaffingPlan, DailyStaffingNeed, AvailabilityRequest, immutable StaffAssignment revisions, AssignmentDay |
| apps.operations | Task, EventNote, EventDocument; manager/staff visibility and private storage boundary |
| apps.workdays | StaffRole, effective-dated CompensationRate, EventMileage, Workday and MileageTrip ledgers |
| apps.event_reports | ExpenseCategory, ManualExpense, IncentiveLine, CostReview; reports and four CSV exports |
| apps.participants | Canonical Participant, account foundation, career profile, forms/submissions/consent, historical import/export, qualifications, private documents, verification and internal review |
| apps.stations | Station/EventStation, official vs demo/test ExperienceSession, staged ExperienceActivity |
| apps.assessments | Categories/question sets/pools, quiz attempts/sections/responses, content administration and kiosk UI |
| apps.games | Versioned event game configuration, GameSession, Duck Hunt implementation |
| apps.simulators | Capture profiles/configuration, authenticated ingestion, accepted/review/voided capture evidence |

Supporting model modules in participants include qualification_models.py, participant_document_models.py, verification_models.py, and participant_review_models.py. Read their services and tests alongside models; model presence alone does not define allowed transitions.

## Relationships

~~~mermaid
flowchart LR
  U[accounts.User] --> SP[StaffProfile optional account link]
  E[Event] --> REG[EventRegistration]
  P[Participant] --> REG
  REG --> QR[QrTicket history]
  REG --> ES[ExperienceSession]
  ES --> EA[ExperienceActivity]
  ES --> QUIZ[QuizAttempt / responses]
  ES --> GAME[GameSession]
  ES --> CAP[SimulatorCapture]
  E --> PLAN[StaffingPlan / daily needs]
  SP --> ASS[StaffAssignment revisions]
  E --> ASS
  ASS --> DAY[AssignmentDay]
  SP --> WD[Workday approved snapshots]
  E --> WD
  SP --> TRIP[MileageTrip approved snapshots]
  E --> TRIP
  E --> OPS[Tasks / notes / documents]
  E --> EXP[ManualExpense / IncentiveLine]
  WD --> REPORT[Event cost and performance]
  TRIP --> REPORT
  EXP --> REPORT
  ES --> REPORT
~~~

Participant uses a stable identity_uuid in addition to its database key. Do not assume every primary key is a UUID or remap identities during handoff. EventRegistration binds one participant to an event. StaffProfile is operational staff identity; it is not a Participant or replacement login. Financial ledgers reference existing events/staff and preserve approved snapshots.

## Authentication and authorization

AUTH_USER_MODEL remains accounts.User. Django Groups/Permissions are the authorization mechanism; do not introduce a parallel role system. ModelBackend serves staff; ParticipantAuthenticationBackend authenticates only active, verified participant accounts. The participant account foundation is not the planned full participant portal.

The internal portal requires an active staff account. StaffProfile account links are explicit. Self-service staff queries are scoped to the active linked profile. Per-action service/view permissions, not link visibility, control mutations, financial reports, private documents, participant data and reviews. The six primary sections are Home, Events, Participants, Operations, Customers, Admin; Customers is currently marked planned. The personal Settings gear is separate from global administration.

CSRF remains enabled. Financial and scheduling writes use transactions, locks, stale revision checks, database constraints and explicit reasons. AuditLog records explicit actor/action/before/after data; audit writes do not use model-save signals. Audit is operational history, not cryptographically tamper-proof evidence. Avoid secrets and unnecessary personal data in audits.

## Backend entry points

| Root / route | Surface |
| --- | --- |
| /internal/ | Staff Home, workspace navigation, personal settings/Quick Actions |
| /admin/ | Restricted Django back office; not the everyday portal |
| /events/ | Event management, ticket workflows, offline/operations/sync |
| /e/<slug>/ | Public event registration |
| /participants/ | Registration/accounts and authorized staff participant tools |
| /staff/ | Staff schedule, requests and directory |
| /operations/ | Tasks, event notes/documents |
| /workdays/ | Daily staff check-in, approvals, trips, compensation configuration/reports |
| /reports/ | Event financial/performance and cross-event reports |
| /stations/; /quizzes/; /games/ | Station and participant experience workflows |
| /api/simulator/captures/ | Bearer-authenticated simulator observation ingestion |
| /events/sync/ingest/; /events/sync/download/ | Trailer/central synchronization boundaries |
| /health/ | Process liveness only; does not check database/providers/readiness |

Services live in domain services/reporting modules. There is no general-purpose customer REST API or HubSpot connector in this active branch. Simulator ingestion is idempotent by submission UUID and validates event/station/profile identity; an HTTP success may mean evidence stored for review, not official completion.

## Hosting and private storage

render.yaml defines the existing btw-preview web service, render-preview auto-deploy branch, PostgreSQL service, /health/, persistent private-file mount, environment setting names and commands. build.sh installs declared requirements and collects static files. Pre-deploy applies migrations; Gunicorn starts the process. Do not modify this infrastructure for the workspace transition.

Private participant/consent/historical roots are distinct from public media/static paths. **Real operational document uploads remain disabled** until a separately configured private durable adapter/root is verified. Synthetic 9D demo PDFs are not a real upload feature. Browser-visible preview access also has an HTTP gate in front of normal Django authorization. No gate credentials are recorded here.

## Environment/configuration names only

No values are supplied. Inspect config/settings/base.py, development.py, production.py, .env.example, render.yaml and domain configuration hooks before using them.

| Category | Names |
| --- | --- |
| Runtime/framework | DJANGO_SETTINGS_MODULE, PYTHON_VERSION, DEBUG, SECRET_KEY, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, STATIC_ROOT, PORT |
| Database | DATABASE_URL, DB_ENGINE, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT |
| Codespaces-provided | CODESPACE_NAME, GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN |
| Preview gate | BTW_PREVIEW_GATE_ENABLED, BTW_PREVIEW_GATE_USERNAME, BTW_PREVIEW_GATE_PASSWORD |
| Private files | PRIVATE_CONSENT_ROOT, PRIVATE_DOCUMENT_ROOT, PRIVATE_HISTORICAL_ROOT |
| Simulator server | SIMULATOR_INGESTION_TOKEN, SIMULATOR_TEST_IDENTIFIER |
| Email/SMS | EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, BTW_TICKET_EMAIL_FROM, BTW_SMS_PROVIDER_URL, BTW_SMS_PROVIDER_TOKEN |
| Trailer/sync/backup hooks | BTW_CENTRAL_SYNC_URL, BTW_CENTRAL_DOWNLOAD_URL, BTW_SYNC_ENDPOINT, BTW_SYNC_API_TOKEN, BTW_TRAILER_IDENTITY, BTW_BACKUP_DIR, BTW_TRAILER_BACKUP_KEY, BTW_BACKUP_FREQUENCY_MINUTES, BTW_BACKUP_RETENTION_COUNT |
| Other settings hooks | OPERATIONAL_DOCUMENT_ROOT, OPERATIONAL_DOCUMENT_STORAGE_ALIAS, OPERATIONAL_DOCUMENT_STORAGE_DURABLE, OPERATIONAL_DOCUMENT_MAX_UPLOAD_BYTES, PARTICIPANT_DOCUMENT_MAX_UPLOAD_BYTES, PARTICIPANT_PORTAL_BASE_URL, PARTICIPANT_SIGNUP_URL |

The last two rows include names accessed as Django settings as well as environment lookups. **An arbitrary environment variable does not automatically become a Django setting.** Confirm the existing adapter/loading path rather than adding configuration during migration. Simulator-helper Windows settings are branch-specific; see 05. Do not print environment dumps, connection strings, credentials, or private local JSON.

## Locked technical decisions

Retain the custom user model, canonical participant/event identities, explicit audit calls, app ownership, server-side permission checks, snapshot/version history, official/test isolation, private-file authorization and environment-specific security. Reuse existing service workflows instead of copying compensation, results or notes into parallel systems. Preserve the portal shell; kiosk fixed-screen geometry is not a management-page layout. See DOCS/ARCHITECTURE.md and current build documents; older audit documents describe historical gaps, not necessarily today's defects.
