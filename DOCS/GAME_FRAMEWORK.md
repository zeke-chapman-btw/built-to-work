# Game Framework (Build 5A)

An Event can have zero or one `EventGameConfiguration`. Without a configuration, the activity flow proceeds from the kiosk assessment to the Simulator; with one, a generic Game activity is inserted before the Simulator. `GameDefinition` identifies an implementation (`key`, `implementation_key`, and version), while Event configuration holds versioned settings.

`GameSession` snapshots mode, game version, configuration version, and settings at start. Its event and `ExperienceSession` must agree. Official, staff-test, and demo modes are copied from the parent experience; test-only completion and reset services reject official sessions. Reset marks a run voided and replacement runs link back through `replaced_by`.

Build 5A supplies configuration, session lifecycle, routing, and a safe placeholder handoff. It does not contain game mechanics, physics, assets, rendering, or Duck Hunt gameplay. Build 5B can implement the `duck_hunt` implementation behind the generic launch contract.
