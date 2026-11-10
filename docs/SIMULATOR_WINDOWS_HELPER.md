# Windows simulator capture helper (Build 6B)

This helper observes SimU on each Windows simulator PC. It captures only the calibrated identifier and upper-right Total Score rectangles and submits completed runs to the Build 6A ingestion API. It never types, clicks, or controls SimU. The Django server remains authoritative for Event/Participant matching, test classification, score validation, and progression. See SIMULATOR_HELPER_CONTRACT.md for the wire contract.

## Architecture and state

simulator_helper/windows.py handles replaceable capture/OCR, calibration.py draws rectangles, core.py contains the hardware-independent engine/API client, and runtime.py runs the loop and diagnostic window. The engine polls only the identifier rectangle before ID capture and only Total Score afterward. It ignores Safety, Productivity, Maintenance, and PASS/FAIL.

WAITING_FOR_ID -> ID_CAPTURED -> WAITING_FOR_SCORE -> SCORE_CAPTURED -> SUBMITTING -> RESET -> WAITING_FOR_ID. Both identifier and score must match for stable_reads consecutive polls at or above min_confidence. The score region must be blank for several polls after the identifier before a score is armed. A score shown before ID capture or left from a prior run cannot be paired with a new ID. After success, the runner waits for the score screen to clear before accepting the next ID. On startup, it likewise waits for a clear score region.

The helper writes one raw ID/score pair, capture time, Event, Station, profile/version, and submission UUID to an atomic local state file before posting. Network failures and 5xx responses retry that exact payload and UUID. An API 4xx rejection retains the pending payload and stops the runner for staff review. A 2xx means stored; the server outcome may still be Needs Review or TEST, not accepted official. Do not delete a pending state file to force another submission. A different run receives a new UUID only after RESET. A restart before the payload is durably prepared discards the partial in-memory run; it will not pair stale partial state with the next run.

## Windows prerequisites and configuration

Use Python 3.11+ on the Windows simulator PC. From the project root in PowerShell, install dependencies with:

    py -m pip install -r simulator_helper/requirements-windows.txt

Install Tesseract OCR for Windows from a trusted distribution. Put tesseract.exe on PATH or set SIMULATOR_TESSERACT_EXE to its absolute path. MSS captures small desktop regions, Pillow preprocesses them, and the local Tesseract CLI reads text. No runtime Internet connection is needed for OCR.

Copy simulator_helper/config.example.json to simulator_helper/config.json and simulator_helper/profile.example.json to simulator_helper/profile.json. These local files are Git-ignored. Fill in the private trailer server URL, active Event UUID, Simulator Station code, existing server profile UUID, and paths. The Event/Station/profile must match an enabled EventSimulatorConfiguration. Put the ingestion token only in the process environment as SIMULATOR_INGESTION_TOKEN. Never place it in either JSON or a log. Keep the Windows account, token, and state file private: the state file temporarily stores the full raw identifier and score. On the private LAN this uses the existing bearer contract. Plain HTTP on an untrusted network would expose the token.

The reserved test identifier tel0000000000 is sent raw like any other identifier. Server-side Build 6A classifies it as nonofficial. The helper does not make business decisions about it.

## Calibrate on the simulator PC

Keep the intended NVIDIA Surround topology, Windows display scaling, and SimU mode active. Coordinates are virtual-desktop pixels, not physical inches or assumed center-monitor coordinates. Calibration saves desktop dimensions and monitor topology. The runner refuses a different topology.

1. Open the SimU username screen. Run: py -m simulator_helper --config simulator_helper/config.json calibrate identifier
2. Drag around only the identifier text/entry area. Review the shown x/y/width/height and use Retry, Cancel, or Save.
3. Complete a test run and display its final report. Run: py -m simulator_helper --config simulator_helper/config.json calibrate score
4. Drag around only the upper-right Total Score; exclude other scores and PASS/FAIL. Optional state rectangles can be saved with calibrate state; they do not control the V1 state machine.
5. Every saved calibration assigns a new profile_version. Transfer the local profile.json to the server by the normal staff-approved method, then run: python manage.py import_simulator_calibration /path/to/profile.json

The import command updates only an existing SimulatorCaptureProfile geometry/version. It neither creates a profile nor changes Event/Station assignment. Create the server profile and EventSimulatorConfiguration through authorized back-office workflow first. Recalibrate and reimport when display geometry changes.

## Diagnostic and normal operation

Run: py -m simulator_helper --config simulator_helper/config.json diagnose

Diagnostic mode shows both live crops, raw OCR text, confidence, valid/stable status, current state, and last stored submission result/status. It never POSTs. It is staff-only because it can show raw identifiers. Check a blank score screen, prefixed and unprefixed IDs, and final Total Scores such as 83.33%, 100%, and 0%. If OCR is uncertain, correct the crop/display/OCR setup; the helper does not invent values.

Run: py -m simulator_helper --config simulator_helper/config.json run

For startup after Windows login, use Task Scheduler under a restricted user with this same command and an environment token available only to that task. Stop with Ctrl+C or stop the scheduled task. Rotating logs record masked IDs, state transitions, submissions, retries, rejection and reset without logging the token. poll_interval, stable_reads and min_confidence limit capture frequency and noise. A network retry uses the same durable UUID. The V1 helper does not include an installer or Windows service manager.

## First trailer test checklist

- Configure private server/token, active Event, Simulator Station, capture profile, and enabled EventSimulatorConfiguration.
- Open SimU login with the final display topology and calibrate the Identifier region.
- Finish a test run and calibrate upper-right Total Score.
- Import the profile on the server; run diagnose and inspect both crops and OCR without submitting.
- Clear the completed result screen, start the helper on a blank score screen, then enter tel0000000000 in SimU and complete a fresh run. Verify the server capture is TEST/nonofficial and does not advance official progression or reporting eligibility.
- Interrupt/reconnect the LAN during a test submission. Verify retry keeps the same submission UUID; 4xx rejection instead preserves pending data for staff review.
- When appropriate, repeat with a real development participant registered to the same Event and confirm only accepted official capture completes Simulator activity.
- Clear the SimU result screen and verify the helper resets before the next run; check a previous score cannot pair with a new identifier.

## Physical validation and limits

The Codespace has no Windows desktop, SimU, or NVIDIA Surround rig. Actual pixels, Windows scaling, Tesseract confidence, blank-score detection, and Task Scheduler startup still require physical simulator validation. Automated tests feed synthetic readings/images and fake API responses. OCR can sometimes return an empty reading for visible but distorted text, so inspect the diagnostic crop before official use. The V1 helper cannot reconcile Needs Review captures or recover a partial run interrupted before durable payload creation; the server stores Needs Review results for later staff reconciliation.
