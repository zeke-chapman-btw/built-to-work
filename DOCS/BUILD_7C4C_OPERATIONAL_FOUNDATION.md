# Build 7C-4C Reconciliation and Virtual Recovery (development)

The encrypted SQLite backup and non-destructive staff reconciliation paths have been exercised in a virtual trailer and central environment. Communications and notification work is outside this pass.

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

The restore action is written to the audit log. After a disaster recovery, operators must configure the replacement environment to use the restored DB and the original trailer identity. The central sync receipt is idempotent, so restoring an older outbox state can safely retry accepted envelopes; the virtual integration runner now verifies this retry path. Physical replacement and production operations still require rehearsal.

## Staff operations visibility

The staff-protected `events:operations-dashboard` page summarizes sync pending/failed counts, last accepted sync, open match reviews, latest backup, ticket-delivery counts, and remaining reserved ticket ranges. It reports whether the central endpoint is configured; it does not actively probe network connectivity. Django Admin exposes read-only metadata for backup records and ticket deliveries. The staff match-review queue is at /events/operations/matches/; staff with view permission can inspect evidence and decision history, while staff with change permission can choose same person or keep separate with a required reason. A same-person decision creates a reversible identity link: neither participant row nor any ticket, consent, registration, answer, or score is moved or overwritten. Only a superuser can reverse a link. Every decision and reversal is audited, and duplicate requests are idempotent.

## Not complete in this checkpoint

- Participant-selected email/SMS preferences and explicit channel consent capture. TicketDelivery records remain an existing queue model; no delivery worker or provider is configured here, and no messages are sent.
- Global/event notification policies, recipient/channel configuration, deduplication, offline notification queue, and retries.
- Live internet/central-server health probes and alerting.
- PostgreSQL encrypted backup, restore, concurrency, retention, and disaster-recovery rehearsal.
- Physical trailer network and printer validation.

Do not treat this development backup workflow as production-ready until key rotation/recovery, offsite access controls, scheduler monitoring, restore rehearsal, and PostgreSQL support are verified.

## Virtual recovery verification

Run python scripts/build_7c4b_virtual_integration.py. The runner uses independent trailer and central SQLite databases and real HTTP. It deletes the original trailer SQLite file before portable restoration. It verifies archive/manifest binding, wrong-key and modified-file rejection, administrator authentication, refusal to overwrite a live database, restored audit history, participant/registration/ticket/consent/group/custom-answer/pending-outbox preservation, and idempotent central synchronization after replay. Temporary data and processes are cleaned up. This does not validate PostgreSQL row locks or physical trailer hardware.

For a replacement trailer, securely retrieve the matching .fernet archive and .manifest.enc sidecar from storage separate from the failed machine, the Fernet key from a separate secret store, the required application configuration, and an active superuser credential present in the snapshot. On a trusted replacement host run: python manage.py restore_portable_trailer_backup --archive /secure/backup.fernet --manifest /secure/backup.manifest.enc --target /new/trailer.sqlite3 --actor ADMIN --confirm. The command prompts for the administrator password without putting it in shell history. The target must be absent. Configure the replacement service to use the restored database and the original trailer identity, then provision the client synchronization token separately and run sync_outbox. Verify the central receipt and local outbox state before serving registration traffic. The earlier restore_trailer_backup --backup-id command remains available only when the original database and its BackupRecord are still readable.

New backups contain an encrypted and authenticated portable manifest holding the trailer identity and archive digest; neither the manifest nor archive contains the encryption key in plaintext. The encrypted SQLite snapshot retains the trailer identity and stored credential hashes. Plaintext client sync credentials and other application secrets remain outside backups and must be recovered from protected deployment configuration or rotated. Backups made before this portable-manifest change cannot use the total-loss command. Losing both the key and its separately protected recovery copy makes the encrypted backup unrecoverable. Protect offsite copies of the paired files and key separately, rehearse replacement recovery, monitor backup success/retention, and verify key rotation procedures before production deployment.

This validation does not prove PostgreSQL row locking, backup/restore, physical trailer storage, printer behavior, or production network resilience. No production credential or participant data is used.
