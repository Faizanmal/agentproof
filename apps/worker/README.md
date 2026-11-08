# Durable run worker

The worker uses `arq` over Redis for at-least-once job delivery. PostgreSQL is the
source of truth: API state changes and `run_events` outbox rows commit together.
The worker publishes committed events, queues `run.queued` deliveries with stable
job IDs, and reconciles queue/lease state after restarts. Duplicate deliveries
reuse the run and the broker's stable job ID; they do not create a second sandbox.

Run it with `make dev` or `uv run --frozen --all-packages arq
agentproof_worker.main.WorkerSettings`. The worker requires the API's database,
Redis, Vultr planner credentials when generating a plan, and the private broker
URL/HMAC configuration for approved-plan execution. An unapproved plan remains in
`awaiting_approval`; no browser job is sent until explicit plan approval. SSE
replay is read from PostgreSQL event IDs, with Redis used only as a notification
and delivery mechanism.

The current host has no usable Docker Engine, and the sandbox VM egress boundary
is not proven. Therefore the repository integration gate can exercise Postgres,
Redis, outbox publication, approval state and SSE replay, but cannot claim an
actual broker-backed browser run or safe public execution. See
[ADR 0006](../../docs/adr/0006-durable-run-workflow.md) for workflow boundaries.
