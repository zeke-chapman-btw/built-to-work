# Build 10C — Base44 reference audit and design gaps

Inspected the authenticated existing BTW Base44 application on 2026-10-21. Entry: https://app.base44.com/apps/6a85ac00cbab4cf3f8a1f6c3/editor/preview . The embedded editor refreshed its preview frames during inspection; a direct application page was then opened through its observed URL and the existing owner Google sign-in, without copying authentication tokens. No data/settings were submitted or changed. Screenshots are private owner-workspace report artifacts outside Git; this document contains no employee, contact, customer financial or authentication values.

## Actual reference observations

| Screen | Observed presentation | Scope/limit |
| --- | --- | --- |
| Events | Heading with short description and compact New Event action; horizontal status text links with a thin red selected underline; rounded event cards with date/location/status and separated staffing footer. | Status navigation was observed. No search/filter control was visible in the inspected listing; Django's real search and advanced filters are retained. |
| Event header / Info | Back link, bold sans-serif record title, short location context, details status, restrained secondary actions. Compact icon/text tabs with underline selection. Facts grouped into lightly bordered rows/cards; contact/housing are separate groups. | Contact values were not transcribed into repository documentation. |
| Staff | Short signup message; compact daily coverage chips; confirmed assignments grouped below with smaller contextual actions. | Django keeps its existing staff-day coverage semantics and history; it does not copy Base44 driver/staff assumptions. |
| Clock | Date selection, attendance and work-report sections with clear empty states and Add Report action. | Django uses daily Workdays/Mileage and its own approval model, not Base44 clock-in/out or pay rules. |
| Expenses | Local Add Expense action, compact semantic table and category/total summary; link to the full report. | Real financial values were excluded from reference crops. No expense was added/edited. |
| Expense modal | Centered light dialog, labeled fields, visible initial focus, compact two-column pair, primary submit plus Cancel/close. Escape dismissed it. | Opened for inspection only; no fields submitted. Existing Django task dialog remains its functional implementation. |
| Tasks | Checklist form with type choices/template action and a clear empty state. | These Base44 checklist/template features are not the same as Django Tasks; no checklist functionality was copied. |
| Notes | Compact category chips, textarea/action, clear no-notes state. | No note was created. Django retains its own note visibility/audit rules. |
| Event Report | Separate page, compact record summary, financial metric cards, grouped report forms and tables. | Django metrics remain derived from authoritative records; editable Base44 lead totals/finalization are not copied. |
| Mobile | Stacked record facts, internally scrolling event tabs, compact actions and a bottom application navigation. | Django retains its existing drawer/shell, using its own responsive action area; no shell replacement was inferred. |

Base44 heading CSS was observed as Roboto sans-serif, 24px/32px, weight 700. Django reuses existing font resources and official branding instead of introducing an external font dependency. Border/radius/spacing observations informed a restrained design; exact Base44 implementation tokens are not claimed as recovered source code.

## Django comparison and decisions

The Build 10B baseline was inspected through source and its actual local synthetic browser review/screenshots. Build 10C visual verification compares its completed local screens with the actual reference above. The report distinguishes source inspection, baseline screenshots and fresh browser checks.

- Adopt compact record hierarchy, text/underline selection, grouped fact rows, smaller secondary actions and clean tables/cards.
- Preserve Django canonical participants/registrations/tickets, permission predicates, audit history, lifecycle, official/test result separation, expense reconciliation, staffing-day calculations and named deep links.
- Events: move + Event before Search; keep validation/query behavior; put Clear filters inside Filters. Status meaning remains unchanged.
- Event workspace: remove prominent report/task header actions; retain Report as an authorized section and one floating event-aware Add Task action. Separate primary navigation from lower-frequency Tasks/Notes/Documents in More.
- Staff: keep assignment management and Workdays/Mileage as separately authorized existing views within the Staff experience. Preserve original URLs and do not grant staffing management to workday managers.
- Expenses and Report: use explicit section projections of the existing reporting backend. Preserve old combined URLs/anchors, calculations, exports and all CRUD/history routes. A visual split is not a financial model change.
- Registration inspection confirms the section selects/creates canonical participants for event registration, issues QR tickets and links to registration/check-in records. Use Registrations & tickets; it is not a full participant profile manager and does not implement Build 11.
- Results: keep recorded metrics and source units, group scores and station controls, and separate expandable configuration/offline/exception references from results. Rename the old configuration-only Event Readiness presentation to Configuration references.
- Shared components and tokens are documented in BTW_UI_DESIGN_SYSTEM.md and implemented with existing Django templates/CSS/JavaScript. No framework or dependency change.

## Evidence and limits

Reference captures include event navigation/Info header, Staff coverage layout, Clock empty state, expense table controls, blank expense dialog, Tasks/Notes empty states, a report form layout and mobile Event presentation. Crops avoid personal/contact and financial record values. Some captures show a smaller editor frame; the direct page supplied the stable desktop/mobile comparison. These observations do not certify unseen Base44 screens, backend behavior or hardware.

No Base44-only feature is represented as implemented Django functionality. No live record was changed. No authentication parameters or complete iframe URLs are retained in these documents or report evidence.

## Additional owner-directed form/menu review — 2026-10-21

The actual Base44 Edit Event page was inspected read-only after owner feedback. It uses a narrow form card, stacked labeled controls, grouped schedule/contact/housing areas, compact related field columns, and a persistent Cancel/Save action bar. Its map/geofence, daily driver/staff needs and structured housing inputs are reference-only domain differences; these are not implemented by this visual refinement. The existing Cancel action returned to the Event without saving. Private contact and employee values are not transcribed into source or report evidence.

The owner explicitly wants the new Django main navigation and overall structure retained. This refinement therefore applies Base44 content density, information hierarchy, icon/text tab styling and form/menu treatment within that shell. Django Event confirmation screens were inspected through GET only; no lifecycle transition or Base44 record was submitted.
