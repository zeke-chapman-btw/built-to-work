# Build 6B — Windows Simulator Results monitor

This package runs locally on the SimU simulator PC. The normal collection path is the desktop XML monitor, not OCR. It reads simulator-generated XML, retains local evidence, and displays recent results. This checkpoint does not send XML results or scores to the BTW Django server.

## Install on the simulator PC

1. Put the repository on the Windows PC with the complete simulator_helper package intact. Install a supported Windows Python with Tkinter.
2. From the repository root, run: py -m pip install -r simulator_helper\requirements-windows.txt
3. Copy simulator_helper\config.example.json to simulator_helper\config.diagnostic.json. Set xml_source_directory to the SimU ExperienceMode XML folder, xml_archive_directory to a writable BTW folder, xml_database_path to a file in that folder, and xml_poll_interval if needed. Do not commit this local configuration. The example's server URL, Event, and Station placeholders belong to the legacy OCR helper and are not used to submit XML monitor results.
4. Run from the repository root: py -m simulator_helper.gui
5. For a desktop shortcut, point the shortcut at the installed pythonw.exe with arguments -m simulator_helper.gui, and set Start in to the repository root. Test the shortcut before using it onsite. Only one monitor instance runs at a time.

The GUI starts monitoring automatically. Settings shows the configured paths, source accessibility, worker status, last successful scan, last archive time, warnings, recent results, and Start/Stop controls. Its Legacy OCR Diagnostics control opens the older capture tools. Tesseract is needed for legacy OCR diagnostics, not for XML monitoring.

## XML handling and local evidence

The monitor reads .xml files from the configured source folder without renaming, editing, or deleting SimU files. It waits for a stable file, retries locked or incomplete files, rejects unsafe XML constructs, and records malformed input for review. Original bytes are copied to the archive's originals folder. A local SQLite index records each observation, parsing outcome, archive status, and duplicate identity. Pending archive writes can resume after restart. An identical XML payload in a distinct file is retained as a distinct attempt; a repeat observation of the same file version is deduplicated.

Participant identification uses LoginCode only when it is exactly tel: followed by ten ASCII digits. The digits are a BTW ticket identifier, not a phone number; leading zeros matter. Unknown or malformed identifiers and uncertain exercise names remain visible for review instead of being linked to a Participant. Known filename patterns identify Dig Footings and Truck Loading. The monitor retains original XML and metadata for later reconciliation.

## Results and limitations

The Recent Results table and Settings show local captures and monitoring health. These local rows are not official BTW Simulator results, do not complete official progression, and do not affect reporting or leaderboards. The official SimU scoring formula is still pending; numerical scores remain Pending Formula. Do not interpret a raw XML field as an approved BTW score.

BTW server ingestion/synchronization, authenticated delivery, Event/registration matching, reconciliation UI, and the earlier server-side calibration import command and test are outside this commit and require separate recovery or verification. The existing Build 6A server ingestion foundation remains unchanged. A future integration must preserve Event-scoped matching, nonofficial test isolation, idempotency, and accepted-official-only progression.

The packaged automated suite covers parsing, retry, archive, deduplication, and recovery with synthetic data. Windows desktop behavior and real SimU file timing still need physical verification. For collection internals and edge cases, see simulator_helper/XML_COLLECTION.md. For the unchanged server-side contract, see docs/SIMULATOR_HELPER_CONTRACT.md.
