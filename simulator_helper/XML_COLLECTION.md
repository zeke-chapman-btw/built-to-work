# Build 6B XML result collection

The desktop monitor's normal workflow watches the configured SimU `ExperienceMode` folder. It reads `.xml` files only; it never renames, deletes, or edits simulator files. OCR capture and score calibration remain available from **Legacy OCR Diagnostics** and are not part of normal monitoring.

## Local storage

- Source: `C:\Users\YOUR_USERNAME\Documents\My Games\SimUCampusData\ExperienceMode`
- Archive root: `C:\Users\YOUR_USERNAME\Documents\BTW\simulator_xml_archive`
- Original XML copies: `C:\Users\YOUR_USERNAME\Documents\BTW\simulator_xml_archive\originals`
- SQLite index: `C:\Users\YOUR_USERNAME\Documents\BTW\simulator_xml_archive\results.sqlite3`

The paths and polling interval are in `config.diagnostic.json`. Each source file must have unchanged size, modification time, and file identity across two scans before it is read. Locked files are retried. Incomplete XML is retried for up to eight seconds after first observation; if it remains malformed, its original bytes are archived and its record is marked for review so later files continue processing.

For crash recovery, the SQLite transaction first stores the source signature, SHA-256, parsed metadata, and original bytes with a pending archive state. The original bytes are then written to a temporary file in the archive directory, flushed, and atomically renamed. Only after that succeeds is the database state marked archived and the temporary database copy released. A restart completes any pending archive from the database copy.

## Duplicate handling and limits

A repeated observation is deduplicated by the resolved source path, device/file identity, size, modification time, creation time where available, and SHA-256. The content hash is not used by itself to suppress a result. Therefore an identical XML payload under a distinct file or distinct file version remains a separate attempt with its own local result ID. This preserves evidence when the simulator can produce identical scorecards.

If the simulator replaces a file while preserving the same path, file identity, size, and timestamps, and the replacement has identical bytes, the operating system exposes no reliable evidence that distinguishes it from the already-seen file. That edge case cannot be resolved without a simulator-provided run ID or sequence number. Normal new files and changed file versions are tracked separately.

## Parsing and score status

XML is size-limited, DTD/entity declarations are rejected, and Python's XML parser is used without external resource resolution. Tickets are accepted only when `LoginCode` exactly matches `tel:` followed by ten ASCII digits. The stored value remains a string, so leading zeroes are preserved. It is a BTW ticket identifier, not a phone number.

Exercise identity is inferred only from the known `Dig Footings` and `Truck Loading` filename text. Other names are retained and flagged uncertain. Header fields, detail fields, unknown field values/attributes, the original filename, and the byte-for-byte XML archive are kept for later review and scoring. `Failure` is shown as reported; a false failure flag does not establish exercise completion. All numerical scores remain **Pending Formula**. The database includes a scoring formula version field for future audited calculations.

## Verification limits

Automated tests use synthetic files in an isolated temporary directory and read the simulator's existing samples without changing them. Those tests verify parsing, retry, archive, and restart behavior; they do not establish desktop UI operation or simulator-generated file timing. Physical verification should confirm a newly generated result appears in the table, its original is present under `originals`, and the monitor survives a close/reopen cycle.

## Interface styling

No approved BTW stylesheet or logo asset was available locally. The monitor uses the Built to Work text name, a black header, white and grey result surfaces, red controls, and a green active status indicator.
