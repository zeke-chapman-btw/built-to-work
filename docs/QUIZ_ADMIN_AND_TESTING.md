# Quiz administration and permanent test participant

Build 7B uses existing assessment categories, questions, choices, versioned question sets, Event links, snapshots, and Build 7A system test identity. BTW superusers administer content; staff can read and preview. Participant kiosk routes and official scoring remain separate.

## Content workflow

1. Open **Quizzes / Tests** in the internal application. Create or update a category. Deactivate an old category instead of deleting it when it has historic use; attempt snapshots keep the original category name.
2. Create a **Draft question set** for the category. Each version contains its own question and answer rows. Enter exactly four answer choices in the authored display order, one marked correct. A question is Easy, Medium, or Hard; its active flag controls eligibility.
3. Add at least 15 valid, active questions. The readiness panel shows totals and difficulty-band gaps. Aim for at least five per band. If a band has fewer than five, the selector fills remaining positions from other eligible bands. Within each difficulty band, questions are sampled randomly. Participant order is Easy → Medium → Hard, and choices remain in authored order.
4. Preview a question using **Kiosk preview**. It reuses the real participant question template, shows a representative 45-second timer, and disables answer submission. It does not create attendance, attempts, or results.
5. Publish the Draft immediately or supply a future timezone-aware effective date. Scheduled sets remain in Published state but are not eligible until their effective time. No background scheduler is needed. To revise published content, create a new Draft version. Existing Event configuration holds a pointer to its selected set; a new version does not automatically replace it.
6. Retire a version when no currently active Event uses it. Historical attempts keep their snapshots and set references.

Question images are optional. Upload a PNG, JPEG, or WebP image under 5 MB. Use a square source, preferably at least 650 × 650 pixels. The measured kiosk image area is 650 × 650 design pixels at 1920 × 1080; CSS uses contain to preserve aspect ratio without cropping. Files are served locally by Django media configuration. The current form performs lightweight signature and size checks; full image decoding should be added as preproduction hardening when an approved image library is available. The upload form can remove or replace a Draft image. Preserve files referenced by official snapshots during media cleanup.

## Event Quiz pools and lock

**Default Pool** resolves the latest effective Published set for the chosen category when Event configuration is saved. **Curated** pins an explicit Published set and may select a subset of at least 15 active questions. Leaving the curated selection empty uses the entire pinned set. Both modes use the same 15-question, 45-second section flow.

The Event configuration page shows readiness before participant use. The Event-to-set link remains fixed. During an active Event, or after completion/archival, normal saves are locked. A BTW superuser may explicitly acknowledge the override and provide a reason; an audit record captures the change. Existing attempts keep question, answer, key, image reference, difficulty, category name, set version, and timing snapshots. Build 9 should strengthen Event lifecycle and readiness checks.

## Permanent BTW Test Participant

Open **Test Participant** from internal navigation as a BTW superuser. Provision the reserved identity if needed, view or print its stable opaque QR card, choose a current test Event, and start or resume a nonofficial run. The same card may be scanned again. Reset requires a reason and only affects the selected test run; history remains visible. The reserved simulator identifier is 0000000000. Do not share the card publicly. Rotation/reissue is a future Admin-only sensitive action.

The permanent test identity and its ExperienceSession, QuizAttempt, GameSession, and simulator captures remain nonofficial and are excluded by Build 7A eligibility rules from official attendance, progression, candidate pools, leaderboards, reporting, and customers. A general fake-data factory is deferred.

## Audit and boundaries

The existing AuditLog captures category creation/editing, set creation/publishing/retirement/cloning, Draft question changes, Event configuration and override reason, test identity provisioning, and test-run start/reset. Models retain created/updated timestamps. Actor identity and before/after details are in audit records. This is a foundation for broader Build 9 auditing.

Build 7C covers public intake/registration and acquisition provenance. Build 9 covers fuller Event lifecycle and readiness. Customer/Campaign access, participant portal, documents/consent, HubSpot, cloud/trailer synchronization, and the Windows simulator helper are outside 7B.
