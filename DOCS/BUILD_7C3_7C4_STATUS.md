# Build 7C-3 / 7C-4 implementation status

Events support registration windows and deadlines, capacities, groups with per-group limits, and event-specific registration questions. Staff can transfer registrations, correct captured answers with a reason, and download branded ticket PDFs without changing ticket numbers.

The offline foundation adds trailer instances, per-event preparations, primary and backup ticket ranges, readiness checks, local envelopes, an idempotent sync outbox with bounded retries, delivery records, match-review records, and backup status records.

The Windows local registration UI, central sync API, encrypted backup/key management, provider adapters, restore/merge workflows, dashboards, notifications, and physical printer/PostgreSQL deployment validation remain outstanding. Public registration still requires an approved consent document; synthetic documents are test-only.
