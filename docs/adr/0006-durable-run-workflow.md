# ADR 0006: Durable run workflow

## Status

**Accepted.** PostgreSQL-backed run workflow is live on the Vultr app VM with the
ARQ worker dispatching approved plans to the private sandbox broker. Local
`make integration` / `make e2e` remain the reproducible CI gates; the Challenge 1
demo additionally proves a real broker-backed browser run against an owned fixture.

## Decision

PostgreSQL owns lifecycle state, lease ownership, idempotency, plan approval
snapshots, step evidence and the replayable `run_events` log. API state transitions
and their event/outbox records share a transaction. A dispatcher publishes committed
rows to Redis and enqueues stable arq job IDs. Redis delivery is at least once; it is
not a source of truth. Consumers deduplicate with run/event IDs and the worker
reconciles expired leases after restart.

The lifecycle is `QUEUED -> PLANNING -> AWAITING_APPROVAL -> RUNNING ->
INVESTIGATING -> COMPLETED`, with `FAILED`, `CANCELLED` and `TIMED_OUT` terminal
edges from active states. A completed run may have verdict `FAIL`. The database
trigger rejects invalid transitions and terminal-state changes. Cancellation is a
database flag until the broker acknowledges termination. Only approved, current
plan digests with valid authorization snapshots may be sent to the private broker.

SSE first authorizes the user, then replays PostgreSQL IDs after `Last-Event-ID` and
uses Redis only to cap concurrent open streams. Event names are an enum and payload
fields are allowlisted before storage. Polling/reconnection reads from the durable
event table, not Pub/Sub history.

## Consequences and limitations

The worker can repeat a task after a crash, so every side effect must use the stable
run/broker job identifier. It holds a renewing lease across provider calls and
broker polling. The broker's SQLite journal and runner artifact bytes are still
separate persistence boundaries. Execution against arbitrary public targets remains
scoped by the sandbox network policy in [ADR 0005](0005-isolated-sandbox-execution.md);
the demo path uses an owned fixture and allowlisted egress only.

## Verification commands

```bash
make integration
make e2e
```

`make integration` creates a disposable loopback PostgreSQL database, migrates it,
runs auth/tenant/workflow checks with the configured local Redis DB 15, and drops
the disposable database in `finally`. `make e2e` also runs the real Chromium tests
when the browser-test image/host prerequisites are available.
