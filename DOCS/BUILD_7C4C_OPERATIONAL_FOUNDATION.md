# Build 7C-4C Operational Foundation (development)

This checkpoint begins, but does not complete, Build 7C-4C.

## Encrypted SQLite backup

The trailer backup command creates a consistent SQLite snapshot, encrypts it with Fernet, writes it outside the primary database directory, stores a SHA-256 ciphertext hash, and records success/failure in `BackupRecord`. The key is supplied separately through `BTW_TRAILER_BACKUP_KEY`; do not store it beside the backup or in source control. Generate a key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and store it in the trailer's secret manager/environment.

Required configuration:

- `BTW_BACKUP_DIR`: mounted storage separate from the primary database directory.
- `BTW_TRAILER_BACKUP_KEY`: Fernet key, stored separately from backup files.
- `BTW_BACKUP_FREQUENCY_MINUTES`: minimum interval; defaults to 1440 minutes.
- `BTW_BACKUP_RETENTION_COUNT`: successful snapshots retained per trailer; defaults to 30. Older ciphertext is removed and its metadata marked expired.

Run `python manage.py create_trailer_backup --trailer <identity>` from a scheduled service at the desired cadence. The frequency guard prevents excessive snapshots if the scheduler runs more frequently. Only SQLite is implemented; PostgreSQL backup/restore tooling remains outstanding.

Restore is deliberately limited to a new, non-existing SQLite file. It verifies the ciphertext hash, Fernet authentication, and SQLite integrity, refuses to overwrite the configured live database or any existing target, and requires a superuser actor. Example:

```sh
python manage.py restore_trailer_backup --backup-id <uuid> --target /secure/recovery/replacement.sqlite3 --actor <superuser-login> --confirm
```

The restore action is written to the audit log. After a disaster recovery, operators must configure the replacement environment to use the restored DB and the original trailer identity. The central sync receipt is idempotent, so restoring an older outbox state can safely retry accepted envelopes; this must still be rehearsed before production.

## Staff operations visibility

The staff-protected `events:operations-dashboard` page summarizes sync pending/failed counts, last accepted sync, open match reviews, latest backup, ticket-delivery counts, and remaining reserved ticket ranges. It reports whether the central endpoint is configured; it does not actively probe network connectivity. Django Admin exposes read-only metadata for backup records, ticket deliveries, and match-review evidence.

## Not complete in this checkpoint

- Staff resolution of uncertain participant matches, including safe identity linking, alias preservation, merge reversal, and associated audit workflow.
- Participant-selected email/SMS preferences and explicit channel consent capture. TicketDelivery records remain an existing queue model; no delivery worker or provider is configured here, and no messages are sent.
- Global/event notification policies, recipient/channel configuration, deduplication, offline notification queue, and retries.
- Live internet/central-server health probes and alerting.
- PostgreSQL encrypted backup, restore, concurrency, retention, and disaster-recovery rehearsal.
- Physical trailer network and printer validation.

Do not treat this development backup workflow as production-ready until key rotation/recovery, offsite access controls, scheduler monitoring, restore rehearsal, and PostgreSQL support are verified.
