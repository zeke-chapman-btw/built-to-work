# Build 7C-4B virtual integration

Run the isolated two-server integration from the Build 7A worktree:

    python scripts/build_7c4b_virtual_integration.py

The runner creates a temporary directory with two separately configured Django
settings modules and two independent SQLite databases, applies migrations to
each, seeds matching event/form/consent configuration plus trailer credentials,
starts two loopback Django servers on free ports, and removes the temporary
files and terminates both processes in a finally block.

The current automated path creates a registration and durable sync envelope in
the trailer database, transfers that envelope over HTTP to the central ingest
endpoint, then verifies the central participant identity, event registration,
original ticket number/token, group, consent evidence, published form version,
and registration answers. It replays the accepted operation to exercise
idempotent acknowledgment and checks invalid trailer credentials and an
unauthorized event scope.

The replay represents the lost-acknowledgment case at the API boundary: central
acceptance occurs before the operation is replayed. The runner currently does
not kill/restart the outbox worker or interrupt a live network transfer; those
worker-recovery scenarios still need dedicated coverage. The two Django HTTP
processes are independent, but the runner directly posts the serialized outbox
payload rather than launching the management-command worker.

SQLite is used because this Codespace has the SQLite runtime but no PostgreSQL
server executable or service. This validates HTTP transfer, isolation, import,
and retry idempotency. It does not validate PostgreSQL row locking or true
concurrent writes. Run concurrency and lock behavior against PostgreSQL before
production deployment.

All data, credentials, ports, and databases in this harness are synthetic and
temporary. No external services or production credentials are used.
