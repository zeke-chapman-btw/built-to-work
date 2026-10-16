# Build 8E-B — Participant directory and unified staff profile

## Scope delivered

Build 8E-B adds a permission-protected staff directory backed by the canonical Participant records and a unified read-only profile. The directory supports name, email, and phone searches, paginates results, and retains the search query across pages. The profile presents only the sections allowed by the signed-in staff member's Django permissions.

Event registrations and ticket numbers require their respective view permissions. Qualifications, private document metadata, quiz results, game results, and historical simulator scores each have separate permission gates. Private document bytes and storage paths are never rendered on the profile; staff must use the existing authorized document workflow to view a file. The profile does not create participants, registrations, tickets, attendance records, or check-in states.

## Routes

- participants:staff-directory — staff participant search.
- participants:staff-profile — permissioned view of one canonical participant.

Both routes require authentication and participants.view_participant. Profile subsections are independently filtered by server-side permissions.

## Validation notes

Automated coverage is in apps/participants/test_staff_directory_8eb.py. The feature uses existing models and does not require a schema migration. Search is bounded to 150 characters and directory pages are limited to 25 records.
