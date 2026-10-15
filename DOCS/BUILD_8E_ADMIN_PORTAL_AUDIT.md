# Build 8E Admin Portal Audit

**Audit date:** 2026-10-15  
**Repository/worktree:** /workspaces/built-to-work-build-8a  
**Branch:** build-8-participant-readiness  
**Expected checkpoint:** 4100b721c90fe729023c0758053e9f4b38299599

> Read-only application audit. No application behavior or data was changed. This document is the only intended new file.

## 1. Executive summary

The project has a working BTW operational portal shell at /internal/ and distinct Django Admin at /admin/. The operational shell is a useful starting point, but it is not yet a cohesive day-to-day participant operations portal. The biggest gap is the combination of missing participant list/profile workflows and disconnected Build 8 staff tools. The shared dashboard is still a placeholder, several implemented screens have no navigation entry, and the Approvals link currently resolves incorrectly.

The main problem is **a combination**: navigation/integration gaps, a missing canonical staff-facing participant directory/profile, and incomplete operational dashboard content. Some underlying functions already exist and should be linked/refined rather than rebuilt. There is also a role-scope concern in event-management views that should be resolved before wider staff use.

A live Codespace preview was available. I verified the operational dashboard and Events list at desktop browser size using the existing development account. The Events empty state loaded (“No events yet”); no participant records were displayed. I did not submit forms, change data, or make approval decisions. Other pages were assessed by source/route/template review, not claimed as runtime-tested.

## 2. Evidence and confidence

| Evidence | What was verified | Limit |
|---|---|---|
| Live browser | /internal/ dashboard and /events/ Events list render; current navigation and visible links; broken Approvals href; Events page repeats its title | One desktop viewport, development environment, empty event data; no tablet/device testing |
| Source review | URL configuration, view permission decorators/checks, templates, CSS/JS, related tests, kiosk design documentation/assets | No test suite was run for this planning-only audit |
| GitHub checkpoint review | Branch’s latest remote commit is the stated Build 8 checkpoint, 4100b721 | Codespace Source Control view displayed “Publish to GitHub” while its remote extension host was failing, so it did not provide trustworthy working-tree status |

**Status caution:** The Codespace workspace showed repeated remote extension-host/Pylance failures and its Source Control view did not expose normal repository changes. The branch/checkpoint were checked against the available GitHub branch history, but I could not independently certify the Codespace working tree as clean. Do not infer a clean tree from the “Publish to GitHub” screen.

## 3. Current portal architecture

- config/urls.py mounts apps.internal_ui.urls at /internal/ and Django’s built-in administration at /admin/. Events, participants, stations and quizzes have their own route namespaces.
- apps/internal_ui/urls.py exposes one landing route. apps/internal_ui/views.py:home requires login and user.is_staff; unauthenticated users are sent to Django’s login page, and non-staff are denied. This is the BTW shell, not Django Admin.
- templates/internal/base.html is the shared shell. It includes the sidebar, top bar, page header, messages, responsive menu script, and global navigation. static/internal/css/tokens.css and static/internal/css/app.css define its current tokens and layout; static/internal/css/content_admin.css supplies shared content/form patterns; static/internal/js/shell.js handles the responsive sidebar.
- The authenticated shell currently routes login/logout through Django Admin. That is an authentication entry point; it does not make /admin/ the operational portal.
- templates/internal/dashboard.html is a starter dashboard: “Welcome to your workspace,” one Participant account requests action, and a note that activity/metrics will appear as workflows are introduced.
- The existing shell is a sufficient foundation. A wholesale replacement is not indicated; it needs a correctly integrated information architecture and consistent staff pages.

### Current visible navigation (live)

Dashboard; Events; Participants (marked PLANNED); Stations; Intake Forms (marked PLANNED); Quizzes / Tests; Test Participant (superuser-only); Campaigns/Users/Settings (marked PLANNED); and a conditional Approvals item. Event operational tools, historical imports/exports, participant documents and qualifications are not represented in the primary navigation. Some operations are linked from event pages or are reachable only by direct route.

### Important routes (from URL configuration)

| Area | Operational entry points | Notes |
|---|---|---|
| Shell | /internal/ | Staff-only landing page |
| Django Admin | /admin/ | Framework data/configuration interface; separate from BTW operational UI |
| Events | /events/; event detail/edit and event-specific registration/configuration routes | Events is in global navigation and rendered live |
| Ticket recovery | /events/tickets/recovery/ | Linked from Events, not a global navigation destination |
| Participant registration form builder | /participants/staff/registration-forms/ and question/preview/publish routes | Staff form workflow, no primary nav link |
| Qualifications | /participants/staff/qualifications/ and participant-specific routes | Staff search and qualification management; not a full profile |
| Documents | /participants/staff/documents/ and category/participant/file routes | Includes authorized private file operations; not linked in primary nav |
| Approvals | /participants/staff/approvals/ and case detail routes | Queue and decision workflow exist; current shell link is malformed |
| Historical import/export | /participants/staff/historical/ and related import/export routes | Staff workflow not connected to primary navigation |
| Trailer/offline operations | /events/operations/, /events/offline/ and related sync/recovery routes | Distinct operational flows; no global Operations navigation group |
| Stations/kiosk | Station management under /stations/; participant kiosk routes are separate | Keep the participant-facing scan/activity experience distinct |
| Quizzes/tests | /quizzes/ | Existing quiz/configuration and activity routes; retain separate operator/participant contexts |

Route names and details should be confirmed from config/urls.py, apps/participants/urls.py, apps/events/urls.py, apps/stations/urls.py and apps/assessments/urls.py when implementation begins.

## 4. Builds 1–8 functionality matrix

Categories: **A** accessible and functional; **B** functional but disconnected; **C** backend exists but UI incomplete/missing; **D** UI exists but is incomplete/broken; **E** portal foundation needed; **F** deferred.

| Capability | Class | What exists / gap | Key source evidence |
|---|---:|---|---|
| Shared operational shell | E | Usable shell and responsive sidebar exist; active-route state, role-aware information architecture, consistent page integration and usable dashboard still need work | templates/internal/base.html; static/internal/css/*; static/internal/js/shell.js |
| Dashboard | D | Route loads, but current page is a thin workspace placeholder rather than an operations dashboard; only account requests are surfaced | apps/internal_ui/views.py:home; templates/internal/dashboard.html |
| Events | A / D | Event list and detail are reachable from navigation and work; live Events page repeats the “Events” heading; view layer uses broad staff-only checks for consequential event operations | apps/events/urls.py; apps/events/views.py:event_list and event staff views; templates/events/event_list.html |
| Participant directory/profile | C | Participant model and adjacent staff workflows exist, but there is no canonical staff participant list/detail/profile route. “Participants” is visibly planned. Qualification search is not a substitute for a participant profile | apps/participants/urls.py; apps/participants/qualification_views.py:qualification_directory |
| Registration and groups/questions | B | Public and offline registration, event questions and group assignment have application workflows; the staff form builder and event configuration are not gathered into a navigable admin workflow | apps/participants/registration_views.py; apps/events/urls.py; apps/participants/urls.py |
| Ticket issuance/recovery | B | Existing ticket creation and staff recovery/reprint routes exist; recovery is surfaced only from event content, not a clear portal destination | apps/events/urls.py; templates/events/event_list.html |
| Qualifications | C | Staff directory/detail and add/update workflows exist, but page is standalone, uses generic inline presentation, and lacks a canonical profile/context/navigation link | apps/participants/qualification_views.py; apps/participants/templates/participants/staff_qualifications.html |
| Participant documents | B | Staff list, category management, upload/replacement/archive and authorized private download workflows exist. They are not exposed in main nav and need consistent participant-profile context | apps/participants/document_views.py; apps/participants/participant_document_storage.py; templates/participants/staff_documents.html |
| Qualification/document approvals | B / D | Approval queue, detail and decisions exist with server-side permission checks and tests; navigation URL is malformed so the live shell link does not reach the queue | apps/participants/verification_views.py; apps/participants/test_verification_8c.py; templates/internal/base.html |
| Historical participant import/export | B | Staff workflows/routes exist, but are not part of the portal navigation/profile workflow | apps/participants/historical_views.py; apps/participants/historical.py |
| Kiosk and stations | A / B | Station management is in navigation; ticket scanning starts an activity. Keep participant kiosk separate from admin. No check-in/attendance workflow should be added | apps/stations/urls.py; station views/templates; Build 7 closure documentation |
| Trailer, offline sync, backup and alerts | B | Offline preparation, sync status/operations, reconciliation, backup and alerts have routes and staff interfaces, but are not discoverable through an Operations navigation group | apps/events/urls.py; apps/events/operations_views.py; Build 7C-4 documentation |
| Django Admin | A (separate tool) | Useful for framework/configuration and superuser operations; not the intended daily BTW staff portal and should not be presented as its replacement | config/urls.py; app admin modules |
| Build 9 scheduling/staffing requests and approvals | F | Deferred roadmap work; expressly outside Build 8E | Future scope |

A classification describes operational reachability and completeness, not whether a model or URL exists in isolation.

## 5. Participant management gap

There is no staff-facing participant index/detail workflow in the inspected participant URL set. The current “Participants” nav item is explicitly marked planned. The qualification directory is a limited search (name/preferred name/email/phone/exact UUID, capped at 50) and leads to qualification records; it does not show a complete canonical participant record with registration history, event associations, contact details, qualifications, documents, verification status and available assessment/results information together.

Build 8E should add a permission-protected staff directory and read-first profile that composes existing records by canonical participant UUID. It should link out to existing workflows rather than copy/duplicate qualification, document, consent, registration or score data. Search results should be minimal and privacy-aware; detail access should be permission checked. Avoid adding new participant data-edit powers until roles, audit requirements and correction policy are agreed.

## 6. Qualifications, documents and approvals integration

- **Qualifications:** qualification_directory and participant-specific views enforce view permissions; add/change actions use separate permissions and require an audit reason. The standalone template does not extend the operational shell and does not provide a return path through global navigation.
- **Documents:** document_views.py applies staff and per-action permission checks, including private downloads. Upload/replacement/archive operations have audit behavior. The main documents template extends internal/base.html, demonstrating the reusable shell path, but the section is absent from global nav.
- **Approvals:** verification_views.py protects queue/review actions; existing tests cover queue and decision behavior. The approval UI templates extend the shared shell. Runtime/source inspection found the shell href incorrectly includes escaped quote characters and places the Approvals anchor outside the primary <nav>. Correct this before treating the workflow as accessible.
- **Participant profile integration:** all three should be reached from a canonical participant profile, while still preserving independent authorization on each endpoint. Hiding a link is not an access-control boundary.

## 7. Kiosk design and reuse

The kiosk design assets are a useful source of BTW identity, not an admin page template. Inspected kiosk sources include static/kiosk/css/kiosk-measured.css, static/kiosk/css/kiosk.css, local font files under static/kiosk/fonts/, logo/image assets under static/kiosk/images/, and docs/KIOSK_DESIGN_STANDARD.md.

Reusable identity elements:

- Brand red #D02030, black #000000, white, neutral grays, and warm yellow #F2B632.
- Existing BTW logo and icon assets.
- Locally served Futura Condensed PT display face and Roboto interface face.
- Clear button hierarchy, strong contrast, focus treatment, status colors and compact card/form patterns.

The current operational portal tokens instead use red #c82032, gray/white neutrals, Arial/Helvetica/system fonts, a 252px dark sidebar and a 1100/760/420px responsive breakpoint system (static/internal/css/tokens.css and app.css). Build 8E can unify the brand tokens and local fonts while retaining admin-specific tables, filters, forms and responsive behavior. **Do not copy the kiosk’s fixed 1920×1080 canvas, --u scale, or kiosk screen geometry into management pages.** The portal is a desktop/tablet data-management interface, not the participant kiosk.

## 8. Recommended navigation

Use permission-aware groups, with route-level authorization maintained independently:

1. **Home** — operational dashboard, recent work and critical alerts.
2. **Events** — event list/detail, event setup, groups/questions, registration settings and ticket recovery.
3. **Participants** — searchable directory and canonical participant profile; profile sections/links for registrations, qualifications, documents, approvals, consent evidence and activity/results where available.
4. **Operations** — trailer/offline readiness, synchronization, reconciliation, backup health, ticket ranges, alert/delivery status and station operations.
5. **Assessments** — quiz/question authoring and event assessment configuration, if used by staff as a distinct workflow.
6. **Administration** — staff/users, configuration and catalog management, visible only to authorized roles. Keep Django Admin clearly separate as a technical admin interface.

Qualifications, Documents and Approvals should be discoverable from both the Participants section/profile and role-filtered work queues, rather than creating an overcrowded set of top-level entries. Intake form configuration and historical import/export can live under Events or Participants according to staff ownership; a small validation workshop should decide this before implementation. Do not include a Check-in/Attendance item.

## 9. Security, privacy and permission concerns

### High priority before broad staff rollout

1. **Event management checks appear broader than least privilege.** apps/events/views.py uses a shared staff_required check based on active staff status for event list/create/edit and related staff actions. This may let any active staff account reach consequential event operations. Review each action and add the existing appropriate Django permissions or explicit role policy before navigation rollout; do not rely on hiding links.
2. **Broken Approvals navigation.** templates/internal/base.html contains escaped quotes around the Django URL tag; live navigation generated a URL containing encoded quote characters. The server-side approval permission checks remain important and should be preserved while correcting the link.
3. **No canonical staff participant profile.** This is principally a product/privacy design gap: the forthcoming profile must avoid exposing full contact/document/evidence details to every staff role by default. Define view permissions and data minimization before combining sections.

### Preserve existing protections

- Dashboard has login/staff checks; qualification, document and approval code has granular permission checks. Keep these checks at the view/service boundary even when adding new navigation.
- Document files use a private, authorized delivery workflow; do not switch them to public media links during UI integration.
- Reuse audit reasons/history for consequential qualification, document, registration and approval actions.
- Existing test-participant visibility is superuser-only; retain this restriction.
- Django Admin and the operational portal are different trust surfaces. Admin-only catalog/configuration controls should not leak into regular staff workflows.

## 10. Prioritized issue list

| Priority | Finding / current behavior | Recommended response | Build |
|---|---|---|---|
| P1 | Event staff views appear to use active-staff status rather than action-specific permission checks | Inventory event mutations and apply least-privilege server authorization; add direct GET/POST permission tests | 8E-A / 8E-D |
| P1 | Approvals appears in shell but malformed live href prevents navigation | Fix URL template syntax, place link inside nav, verify role visibility and direct endpoint permissions | 8E-A |
| P1 | “Participants” is planned; there is no staff directory or canonical profile | Add searchable staff directory/profile using existing participant UUID and read-only related sections first | 8E-B |
| P2 | Dashboard is a starter placeholder and does not summarize operational workload | Replace with permission-filtered links/counts/alerts from existing workflows; no duplicate source-of-truth data | 8E-A / 8E-D |
| P2 | Qualifications and other Build 8 tools are not consistently integrated; qualifications page is standalone | Wrap in shared shell and link profile, documents, approvals, registration and history by permissions | 8E-C |
| P2 | Operations, historical import/export, registration-form builder and ticket recovery are hard to discover | Add a small Operations/Participants/Events information architecture with correct active route state | 8E-A / 8E-D |
| P2 | Events page showed duplicate heading and Dashboard remained visually active on non-dashboard route | Use one page-title source and derive active nav from current route | 8E-A |
| P3 | Admin and kiosk typography/colors are not yet a unified design system | Reuse approved BTW tokens/fonts, add admin UI tokens/components; retain responsive data layouts | 8E-A |
| P3 | Responsive source rules exist but tablet/mobile usability was not verified | Add target viewport checks and test dense tables/forms at tablet widths | 8E-D |

Severity reflects audit assessment, not a formal security test. Confirm exact permission names and role policy against the deployed staff groups before changing authorization.

## 11. Proposed Build 8E phases

### 8E-A — Portal foundation

**Deliverables:** repair Approvals href; establish permission-aware navigation groups and route-active state; align page title/breadcrumb conventions; create reusable admin tokens, typography, buttons, form controls, tables, status badges, empty states and alerts; improve dashboard into a useful workflow hub using existing queues.

**Reuse:** internal/base.html, internal/dashboard.html, internal CSS/JS, existing views and Build 8 queues, kiosk logo/fonts/brand tokens.

**Expected areas:** templates/internal/*, static/internal/css/*, possibly context/navigation helpers and tests. Do not alter kiosk layouts.

**Acceptance/tests:** all permitted nav links resolve; non-permitted links are hidden; direct requests remain protected; active state and breadcrumb are correct on every linked page; dashboard empty states are useful; responsive layout works at approved desktop/tablet sizes; existing route tests pass.

**Exclusions:** business logic, check-in/attendance, Build 9 scheduling.

### 8E-B — Participant integration

**Deliverables:** staff participant directory with privacy-aware search and pagination; canonical participant profile keyed by UUID; summary of contact data and event registrations; links/sections for existing qualifications, documents, consent/verification, custom registration answers and assessment/simulator outcomes only where those records exist.

**Reuse:** participant/event/registration/qualification/document/verification models and existing service/views; do not duplicate facts into a new profile table.

**Expected areas:** participant staff views/URLs/templates, profile composition/query helpers and permission tests.

**Acceptance/tests:** authorized staff can find and open a participant; permissions limit search and fields; UUID/registration associations match source records; no hidden/archived evidence is exposed improperly; records without event registration still display; no participant mutation occurs from read-only profile sections.

**Dependencies:** agree staff role matrix and which contact/assessment fields each role may see. Implement edits only if separately approved.

### 8E-C — Qualifications, Documents & Approvals integration

**Deliverables:** put qualification pages inside shared shell; add profile context/backlinks; integrate document and approval queue links; present verified/self-reported, current/archived/replaced and decision history statuses consistently; retain dedicated permission-checked private-file and decision endpoints.

**Reuse:** Build 8A catalog/mappings and qualification provenance; Build 8B private documents; Build 8C approvals/reviewer history and tests.

**Expected areas:** qualification_views.py, document_views.py, verification_views.py, participant templates and shell navigation.

**Acceptance/tests:** links appear only for roles with corresponding permissions; direct URL and POST checks still deny unauthorized users; private documents remain private; decision/audit history is preserved; no old qualification or approval data is rewritten.

### 8E-D — Events/operations integration and closure

**Deliverables:** link event setup, ticket recovery, registration form configuration and existing offline/operations dashboard; add role-filtered operational summary; review all workflows at desktop/tablet sizes; resolve duplicate headings and nav state; regression and security review.

**Reuse:** existing Events/stations/assessment/offline/sync/backup/alerts/reconciliation and historical import/export routes; existing virtual sync/recovery runners.

**Acceptance/tests:** staff can follow intended event and trailer workflows from the shell; event and trailer scopes/permissions are enforced; route links work without manual URL entry; operations statuses accurately represent source data; no check-in, arrival or attendance-scanning feature is added.

**Dependencies:** 8E-A navigation and 8E-B profile; role review from 8E-A. PostgreSQL/physical/real-provider tests remain separate deployment validation.

The four phases are sufficient. No additional portal foundation build is justified by this audit.

## 12. Deferred Build 9+ functionality

- Event scheduling, staffing requests, approvals, and related Build 9 workflows (F; excluded from this audit’s implementation plan).
- Any future participant/employer/customer portals not yet scoped.
- No event check-in, arrival tracking, attendance scanning or “checked in” status. Existing legacy Attendance schema/records, if present, are not an active operational workflow and should remain preserved unless separately authorized for a data migration.

## 13. Open decisions before implementation

1. Which staff roles may view participant contact details, consent evidence, qualifications, private documents, assessments and simulator results?
2. Should participant profiles be read-only in 8E-B, with corrections remaining in existing audited workflows?
3. Which groups may create/edit events, registration questions, groups, ticket recovery and offline activation?
4. Should Operations be one top-level nav group or a dashboard plus event-specific operational tools?
5. Which dashboard alerts/counts are actionable and safe for each role, and what should staff see when there are no events or pending records?
6. What tablet viewport and browser/device are the acceptance target? Is phone support required for staff, or only tablet/desktop?
7. Should historical import/export be grouped under Participants or Administration, and which roles can export sensitive data?
8. Confirm that the approved local kiosk fonts/logo are licensed/approved for internal portal use as well as the kiosk.

## 14. Final assessment

The operational portal shell exists and should be reused. It is not yet a complete operational workspace. Build 8E should focus on integrating existing workflows, creating the missing canonical staff participant directory/profile, correcting authorization/navigation issues, and applying a consistent BTW design system to desktop/tablet workflows. The first implementation checkpoint should resolve the broken Approvals link and event mutation permission scope before expanding menu access.

**Audit scope:** source/routes/templates/CSS review plus live desktop preview of the dashboard and Events list. No application code, database, participant record, approval, communication, migration, or Base44 data was changed. No tests were run as part of this read-only planning audit.