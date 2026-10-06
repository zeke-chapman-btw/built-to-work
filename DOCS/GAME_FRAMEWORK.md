# Game Framework (Build 5A)

An Event can have zero or one `EventGameConfiguration`. Without a configuration, the activity flow proceeds from the kiosk assessment to the Simulator; with one, a generic Game activity is inserted before the Simulator. `GameDefinition` identifies an implementation (`key`, `implementation_key`, and version), while Event configuration holds versioned settings.

`GameSession` snapshots mode, game version, configuration version, and settings at start. Its event and `ExperienceSession` must agree. Official, staff-test, and demo modes are copied from the parent experience; test-only completion and reset services reject official sessions. Reset marks a run voided and replacement runs link back through `replaced_by`.

Build 5A supplies configuration, session lifecycle, routing, and a safe placeholder handoff. It does not contain game mechanics, physics, assets, rendering, or Duck Hunt gameplay. Build 5B can implement the `duck_hunt` implementation behind the generic launch contract.

## Build 5B: playable Duck Hunt

The generic launch dispatcher now resolves implementation_key=duck_hunt to the immersive Duck Hunt adapter. No models or migrations are added. Other Games and the no-Game path retain the Build 5A contracts. The old placeholder HTTP completion endpoint rejects Duck Hunt: a playable run must complete through validated result acceptance.

### Lifecycle and integrity

- Launch reuses the generic GameSession, snapshots Duck Hunt V1 rules, seed and generated targets, and waits in NOT_STARTED. Loading a page never starts the round.
- Activating Start once records a server start deadline after the 3-2-1-GO countdown (3.6 seconds). The server rejects results before another 45 seconds have elapsed.
- The browser sends a bounded list of trigger times and aim coordinates, never an accepted score. The server validates shape, finite coordinates, bounds, monotonic timing and ownership; it replays deterministic hit detection against the stored schedule and calculates all metrics.
- Atomic acceptance delegates to complete_game_session. Its first result wins on retry; voided/replaced runs are rejected. Event, experience, mode, completed quiz and browser session authorization are checked. Official runs cannot use Test replay.
- This is reasonable local-kiosk integrity, not a competitive anti-cheat system. A hostile client could synthesize a plausible shot sequence. The authoritative duration and recomputed score prevent arbitrary posted score fields; no per-frame requests are made.
- Refresh before activation is safe. Refresh during countdown/gameplay shows ROUND INTERRUPTED and accepts no invented result. A network save failure offers retry of the same in-memory ledger. Full resume/staff recovery is deferred to Build 5D.

### Rendering and input

The standalone template uses a fixed 1920x1080 logical canvas scaled proportionally to the viewport, without scrolling or normal kiosk navigation. Local artwork and locally installed BTW fonts are used. Canvas background is cached; one requestAnimationFrame loop draws targets, restrained water motion and fading point feedback. Device pixel ratio is capped at two.

AimTriggerInput emits device-independent aim and trigger events. PointerAdapter maps browser primary pointer movement/clicks into logical coordinates. Reticle and hit testing use the same coordinates. Sinden can feed this one path in Build 5C; no SDK/calibration or separate scoring engine is present.

### Versioned rules, opportunities and scoring

apps/games/duck_hunt.py owns RULES. Each GameSession stores the complete rules/target snapshot and implementation version 1, in addition to its Event configuration version. V1 deliberately uses code-based rules; arbitrary Event settings do not override timing/scoring. Tuning future sessions requires a rules/version change; existing snapshots remain unchanged.

39 scheduled targets span three phases: 0-14s, 14-30s, 30-45s. Fixed per-phase distance/speed counts are shuffled using a server seed; entry direction, height, slight wave and 0-80ms spawn jitter vary. Path families cycle level/rising/descending. No performance-adaptive difficulty is used. Planned concurrency grows approximately 1-3, 3-6 and 6-10; hits naturally reduce visible concurrency. Late flights truncate at the hard round deadline.

| Distance | Slow | Medium | Fast | Nominal width |
| --- | ---: | ---: | ---: | ---: |
| Near | 10 | 15 | 20 | 160px |
| Mid | 20 | 25 | 30 | 126px |
| Far | 30 | 35 | 40 | 98px |

Flights last 7.0/5.7/4.5 seconds. Hit detection uses the same body ellipse in Python and JavaScript. Overlap resolves nearest/largest first, then higher target ID, matching paint order. One trigger hits at most one target; hit targets disappear immediately, score/kills update, and +points fades over 700ms. Misses/escapes incur no penalty. Rapid primary triggers are counted; the HTTP ledger has a defensive size limit (4,500 shots), not an ammunition/reload mechanic.

### Results and data decision

GameSession retains generic raw_score/status/timestamps/mode and version/configuration snapshot. result_data.duck_hunt stores score, kills, shots, accuracy (zero-safe), opportunity count, distance/speed aggregates, rules/implementation version, mode and authoritative round start/end. Generic completed_at records acceptance time. The submitted shot ledger is not retained.

JSON aggregates are intentional for V1: no leaderboard/percentile/reporting query needs exist yet. No Duck-only columns are added to GameSession. A future indexed Duck result table can be introduced when concrete query requirements justify it.

Results show only Score/Kills/Accuracy with the subdued hunting background, then NEXT UP SIMULATOR. Continue uses the existing generic Simulator-next placeholder. Neither Simulator nor leaderboard functionality is simulated.

### Test Mode and manual review

The reserved KIOSK-UI-TEST Event is the development configuration. It must have no Participant registrations, Attendance or official experiences. Configure its existing EventGameConfiguration to the seeded duck_hunt GameDefinition in a development database only; do not change a live Event for review. The existing setup_kiosk_review command prepares synthetic assessment categories and image-bearing questions.

1. Activate the project's virtual environment; start python manage.py runserver 0.0.0.0:8000. Open the private forwarded port.
2. Open /stations/operate/KIOSK-01/. Hold the BTW logo about three seconds; choose Start Test Mode.
3. Complete the real assessment with two categories (timed expiry also completes each section). On Quiz Results use the configured Duck Hunt handoff.
4. Move the mouse to aim and click the large Start target. Observe 3-2-1-GO, then 45 seconds of gameplay.
5. Click ducks. Check reticle alignment, readable near/mid/far targets, smooth motion, increasing concurrency, immediate disappearance/+points, Score and Kills, and no negative miss feedback.
6. At zero, verify Score/Kills/Accuracy results over the subdued environment. Continue reaches Simulator-next.
7. In Test Mode use PLAY AGAIN on game results/interruption to void/link only the nonofficial GameSession and replay without repeating the quiz. RETURN TO KIOSK clears the existing Test Mode browser context. Test runs create no identity, registration, ticket or attendance.
8. Official review requires a legitimate checked-in development registration, QR and completed official quiz on an Event configured for Duck Hunt. Do not use official data merely to review visuals. Official sessions do not show replay/test controls.

Inspect the start, HUD, moving targets, results and Test actions on the physical 32-inch display at 1920x1080: no clipping/scrolling, comfortable aim target sizes, readable fonts at 2-4 feet, and fair hit regions. Browser viewport checks do not substitute for physical Sinden/performance testing.

### Validation and remaining scope

Python tests cover routing, ownership/event/mode, timing, scoring, overlap, duplicate/void/replacement safety, progression, nonofficial isolation, schedule comparability and replay. Node tests cover deterministic client hit logic separately from rendering. Run the standard full Django suite, check, migration dry-run, git diff --check and node --check on all Duck Hunt JS.

No audio is required or included. Build 5C covers physical Windows/Sinden calibration and input validation. Build 5D covers full interruption/recovery and final gameplay polish. There is no simulator, admin game editor, sync, percentile or leaderboard in Build 5B.
