# Build 7D — Historical participant import/export

This is a staff-only migration tool. It does not create consent acceptance, event registrations, tickets, QR codes, official simulator captures, or assessment attempts.

## Import

Open Participants → staff/historical/ as a BTW staff account with the historical import permissions. Upload a UTF-8 CSV or Excel .xlsx file (up to 5 MB; 10,000 rows; 100 columns). The source is retained in PRIVATE_HISTORICAL_ROOT outside public media. The app stores its SHA-256 digest and the original filename. The operator maps columns, can save the mapping, and previews every nonblank row before committing.

First and last names are required. UUIDs and Event UUIDs, dates, and simulator scores are checked. A historical score requires its original scoring method and source. Optional event names/dates/groups and scores are stored as separate historical records. An Event UUID can link a historical event record to a known Event, but no official EventRegistration is issued. An imported person can have no Event.

Rows with missing fields or bad column counts appear with an error. Possible matches by UUID, email, phone, or name plus birth date are held for explicit staff review. A reviewer must choose to link to a verified person or keep records separate and enter a reason. Existing participant fields are not overwritten. Rows import individually in database transactions; failed rows remain visible. Repeating a committed batch is rejected.

## Export

Authorized staff can choose fields and filter by Event, event date, group, registration source, or no Event. CSV and .xlsx are available. Historical events and original score values carry their source and method. Cells that spreadsheet programs could interpret as formulas are escaped. Each export records operator, selected fields, filters, row count, and timestamp. Limit: 50,000 rows per export; narrow filters for larger datasets. Exports contain sensitive participant data and must be transferred and retained under BTW privacy controls.

## Permissions and deployment

The upload, mapping, review, and export URLs check Django permissions server-side. Grant only the required participants historical-model permissions to authorized BTW staff. Use HTTPS, restrict access to the private file root, and keep PRIVATE_HISTORICAL_ROOT outside any web-served directory. Back up source files and the database together; the source spreadsheet is provenance and may contain personal data. No document is placed in Git. openpyxl is required for .xlsx.

## Limits

The import is intentionally conservative: possible duplicates do not auto-merge; historical scores remain separate from official Simulator captures and leaderboards. It does not infer past consent or issue a replacement ticket. Source spreadsheet formulas are rejected for mapped participant values. PostgreSQL concurrency and a real historical customer file have not been tested in this development pass.
