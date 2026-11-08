# AgentProof

Authorized browser QA with evidence-derived findings and containment-first sandbox
execution. Accounts, cookie sessions, verified-email policy, tenant-scoped
workspaces/projects/plans/runs, Vultr Serverless Inference planning, approval gates,
replayable SSE, a private sandbox broker, and disposable Playwright runners are
implemented. The live Challenge 1 demo runs on Vultr at
[https://agent.faizanmalik.me](https://agent.faizanmalik.me).

## Fresh clone quick start

Run all commands from the directory containing this README (`agentproof/`).
Install the pinned Node and Python versions from `.node-version` and
`.python-version`, pnpm 11.18.0, uv 0.8.3, and Docker with Linux-container Compose.
Make is optional. `corepack --version` should be checked before relying on it;
Corepack is absent on the implementation host. Existing pnpm works without it.
Installing missing global tools is an explicit machine setup step, not something
the project performs silently.

```bash
make bootstrap
make db-up
make migrate
make dev
```

Bootstrap installs frozen JavaScript and Python dependencies and copies
`.env.example` to `.env` only when `.env` does not exist. Check that file before
starting services. `make db-up` starts Redis only; FastAPI and migrations connect
to the PostgreSQL database configured by your existing `DATABASE_URL`. Preserve
your local credentials in `.env`; they are not printed or replaced by bootstrap.
Bootstrap initializes empty local signing/encryption keys securely; inference
keys stay empty. Existing keys are preserved. Do not reuse these
credentials in production or paste real keys into chat or Git.

Open [the local UI](http://localhost:3000), [API liveness](http://localhost:8000/api/v1/health/live)
or [development API docs](http://localhost:8000/api/v1/docs). The Projects page can
check actual API readiness. An unavailable database or Redis yields HTTP 503.
`Ctrl+C` stops the dev supervisor. Data services continue until explicitly stopped.

### PowerShell equivalents (no Make required)

```powershell
python scripts/manage.py bootstrap
python scripts/manage.py db-up
python scripts/manage.py migrate
python scripts/manage.py dev
```

If PowerShell blocks the pnpm `.ps1` shim, use `pnpm.cmd`; the portable task script
already does this. It also selects a workspace-local uv cache. For direct uv calls:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.cache/uv'
uv sync --frozen --all-packages
curl.exe -fsS http://localhost:8000/api/v1/health/live
```

On POSIX shells the equivalent cache setting is `export UV_CACHE_DIR="$PWD/.cache/uv"`.

## Commands

Each Make target maps to `python scripts/manage.py <target>` on every platform.

| Target        | Action                                                                                        |
| ------------- | --------------------------------------------------------------------------------------------- |
| `bootstrap`   | Frozen pnpm and uv workspace installation; create local env if absent                         |
| `dev`         | Start loopback-only API, Next dev server and arq worker; stop all on exit                      |
| `db-up`       | Start Redis and wait for its health check; use configured local PostgreSQL                    |
| `migrate`     | Apply Alembic revisions (initial empty baseline)                                              |
| `test`        | Unit tests and generated OpenAPI freshness check                                              |
| `lint`        | Ruff, Python formatting, ESLint and Prettier checks                                           |
| `typecheck`   | Strict mypy, Next type generation and strict TypeScript                                       |
| `build`       | Production Next build and three Python distributions                                          |
| `smoke`       | Local HTTP endpoints, UI pages and same-origin proxy; requires both services                  |
| `integration` | Disposable PostgreSQL migration round-trip and auth/tenant/workflow tests; data services required |
| `e2e` | Integration gate plus real Chromium runner tests; PostgreSQL, Redis and installed Chromium required |
| `contracts`   | Export FastAPI OpenAPI and regenerate TS types                                                |
| `security`    | detect-secrets on tracked and nonignored candidate source files                               |
| `format`      | Apply Ruff and Prettier formatting                                                            |

```bash
make lint
make typecheck
make test
make security
make build
make smoke
```

Dependency edits require intentionally updating and committing `pnpm-lock.yaml`
and/or `uv.lock`; normal installs use `--frozen-lockfile` and `--frozen`.
CI also validates Compose, migrates a real local database and tests readiness
failure/recovery with Redis stopped. A workflow file is not evidence of a hosted
CI run; see the phase report for actual local results.

## Architecture and boundaries

```text
Browser -> Next.js :3000 -> /api/v1 rewrite -> FastAPI :8000
                                              | existing PostgreSQL from `DATABASE_URL`
                                              | Redis :6379 -> arq worker
                                              | PostgreSQL run_events transactional outbox
Future: Vultr Caddy TLS -> Next.js (same routing strategy)
API outbox -> arq/Redis worker -> authenticated private broker -> disposable Playwright runner
```

Next.js owns all API rewrites. Caddy's future configuration forwards the whole
origin to Next.js; no second API routing mechanism is configured. See
[ADR 0001](docs/adr/0001-architecture.md). Contracts are generated from FastAPI;
the browser uses a typed same-origin client and holds no API credentials.

| Port | Purpose       | Local binding |
| ---- | ------------- | ------------- |
| 3000 | Next.js       | Loopback      |
| 8000 | FastAPI       | Loopback      |
| 5432 | Existing local PostgreSQL (not started by default Compose) | user-managed |
| 6379 | Redis 7       | `127.0.0.1`   |

API, worker and runner Dockerfiles use nonroot runtime users and frozen installs.
The runner image installs pinned Playwright Chromium; the broker runs as a separate
host service on the sandbox VM because it alone controls that VM's dedicated Docker
Engine. No API, web, worker or runner container has a runtime socket. The live
Challenge 1 deployment uses `infra/compose.prod.yml` on Vultr; local Compose remains
for development only.

Optional containerized application path:

```bash
docker compose --env-file .env -f infra/compose.dev.yml --profile app up -d --build --wait
docker compose --env-file .env -f infra/compose.dev.yml exec api alembic -c apps/api/alembic.ini upgrade head
```

The containerized API cannot use a `127.0.0.1` database address because that
resolves inside its own container. Set `DATABASE_URL_CONTAINER` in `.env` to the
same database and credentials as `DATABASE_URL`, but with `host.docker.internal`
as the host. PostgreSQL must listen on the Docker host gateway and allow that
connection. Alternatively, opt into the bundled local-only PostgreSQL with
`docker compose --env-file .env -f infra/compose.dev.yml --profile local-postgres up -d --wait postgres`
and set `DATABASE_URL_CONTAINER` to its `postgres` service address. The bundled
service is not started by `make db-up` and does not take port 5432 unless explicitly
requested.

Stop host dev servers first to avoid port conflicts. The standalone web image
builds its rewrite for the private `api:8000` origin; changing that origin requires
a rebuild. This is local development infrastructure, not production isolation.

## Troubleshooting and cleanup

- Docker permission/pipe errors: ensure Docker Desktop is running with Linux
  containers and the current user/process has permission. Check `docker info`.
- `connection refused` during install: package registry access is required on first
  install. Cached dependencies may work offline; do not manufacture lockfiles.
- Readiness 503: inspect `docker compose -f infra/compose.dev.yml ps` for Redis and
  confirm `DATABASE_URL` reaches local PostgreSQL and `REDIS_URL` reaches Redis.
  Liveness stays 200 even when either dependency is unavailable.
- PostgreSQL port conflicts: default development uses your existing local PostgreSQL,
  so `make db-up` does not bind 5432. To use the optional Compose database, select
  an unused loopback port, for example in PowerShell:

  ```powershell
  $env:POSTGRES_PORT = "15432"
  $env:DATABASE_URL = "postgresql+asyncpg://agentproof:agentproof_local_only@127.0.0.1:15432/agentproof" # pragma: allowlist secret -- public local-only fixture
  docker compose --env-file .env -f infra/compose.dev.yml --profile local-postgres up -d --wait postgres
  python scripts/manage.py migrate
  python scripts/manage.py dev --api-port 18000 --web-port 13000
  # In another terminal, with services running:
  python scripts/manage.py smoke --api-port 18000 --web-port 13000
  ```

  POSIX equivalents use `export POSTGRES_PORT=15432` and `export DATABASE_URL=...`.
  Compose also accepts `REDIS_PORT`, `API_PORT`, and `WEB_PORT` host overrides.
  Adjust `REDIS_URL` if the Redis host port changes.

- Migration errors: confirm database health, then rerun `make migrate`. Never use
  `create_all` to hide a migration problem.
- `make smoke` fails: start `make dev` in another terminal and run `make db-up`.
- `docker compose -f infra/compose.dev.yml down` stops local services and retains
  named database volumes. Removing volumes destroys local data; this is never
  performed by bootstrap, smoke tests or CI cleanup.
- Logs use server request IDs. Raw credentials, request headers, bodies and query
  values are excluded from application request logs. Do not enable raw access
  logging around future credential flows without reviewing redaction.

Browser execution is restricted to broker-managed disposable sandboxes. The live
Vultr demo runs contained Playwright jobs against an owned fixture; see
[Vultr Challenge 1 deployment](#vultr-challenge-1-deployment) and
[sandbox notes](infra/sandbox/README.md). No production security claim follows
from successful local Docker operation alone.

## Account and data workflow

Start data services, apply migrations, then start the API/web supervisor. Open
/auth to register. Open the development verification message from the signed-in
account and confirm the one-time link. Verified email is required to approve plans
or queue runs. /projects lists real workspaces and projects and supports creation.
Account credentials stay in HttpOnly cookies; the typed client holds only user data
and the separate CSRF value in memory.

On the current implementation host, PostgreSQL and Redis are already listening on
5432 and 6379. `make db-up` cannot bind those ports while they are in use, and it
must not be used to replace those processes. Point `DATABASE_URL` and `REDIS_URL`
at the running local services. Fresh clones still use the template defaults, and
Compose commands read the root `.env`. The older 15432 override is historical.

Run the real local-account smoke while the API is running:

```powershell
uv run --frozen --all-packages python -m agentproof_api.cli smoke
python scripts/manage.py integration
```

Integration creates a uniquely named disposable database on the configured local
Postgres port, runs upgrade/downgrade/upgrade/drift checks, uses rollback/savepoint
fixtures for API tests, and drops the disposable database in finally. It uses Redis
DB 15 in this project's local container and serializes integration invocations.
Never point this task at a shared or production service. The CLI smoke is guarded
to development loopback URLs and intentionally retains its real local test account
and workspace. It prints no passwords, session values or verification tokens.

Production mail requires SMTP configuration; the dev mailbox is absent there.
An operator invokes the bounded delivery job explicitly:

```powershell
uv run --frozen --all-packages python -m agentproof_api.cli deliver-mail
uv run --frozen --all-packages python -m agentproof_api.cli cleanup-artifacts
```

No mail delivery is scheduled automatically. Configure the private operator job,
production secrets, TLS and proxy trust before release. SMTP delivery has not been
verified against an external provider. See [ADR 0002](docs/adr/0002-data-authentication.md)
for role permissions, token replay behavior, verification, retention and key rotation.
The complete request/response/error contract is [OpenAPI](packages/contracts/openapi.json).
SSE replay uses Last-Event-ID or after_id; follow=false returns currently persisted
rows without polling. List routes use limit<=100 and signed, scope-bound cursors.
### Run evidence and reports

Runner traces are the only source of execution assertions. The API imports
runner output after broker completion, redacts structured trace fields, hashes
the bytes, records a redaction audit, and stores them outside the web static
directory. In development, `ARTIFACT_STORAGE_ROOT=./artifacts` is private local
storage. Production requires an absolute path on a private encrypted Vultr
block-storage mount; never put this directory under `apps/web/public`. The
repository does not enable a public object bucket or estimate storage pricing.
The user must be signed in and authorized for the run to download an artifact;
downloads are attachment-only and integrity checked. Links expire with their
artifact retention window (seven days by default).

Open a report at `/runs/<run-id>/report`, or request the authenticated API JSON
or Markdown report and the Playwright ZIP export. The web report renders text
through React (no raw HTML insertion). Unsupported or unexecuted plan steps are
marked `TODO_MANUAL`; exported tests use test-account environment references,
never stored credentials. A timeout remains inconclusive unless an assertion
provided a concrete observed mismatch. See [ADR 0007](docs/adr/0007-private-evidence-and-reports.md)
for the redaction boundary and storage limitations.

## Authorized targets and TEST logins

See [ADR 0003](docs/adr/0003-origin-proof.md) for the precise authorization model and limits. A verified workspace owner/admin issues a single-use verification-challenges request, publishes the returned token by DNS TXT or HTTPS file, then confirms with verify-target. The authorized-origins API lists expiring grants; target-policy accepts only verified origins and same-project secret IDs. The projects screen provides proof and TEST-login forms. Public targets require HTTPS; demo attestation requires the administrative exact allowlist and defaults off.

Credentials require TEST-account confirmation. Responses expose only ID, label and creation time. No public decrypt endpoint exists. Runs capture signed immutable policy snapshots. Browser dispatch uses the private broker under the network policy in [ADR 0005](docs/adr/0005-isolated-sandbox-execution.md). See [ADR 0003](docs/adr/0003-origin-proof.md) for key rotation and one-use credential delivery boundaries.

For a reproducible real HTTP demo check, run the command below with local dependencies available. It uses an operator-owned fixture on 19090 and a temporary API on 18003, enables demo configuration only in that child process, removes its project and stops both servers. It retains a local smoke account/workspace and writes only redacted API logs under ignored .cache.

    uv run --frozen --all-packages python scripts/authorization_smoke.py

## Vultr Challenge 1 deployment

Production demo (keep up for judging; destroy by **2026-11-14 18:00 IST**):

- **URL:** https://agent.faizanmalik.me
- **Stack:** two Vultr VMs (app `vhf-3c-8gb` + sandbox `vhf-2c-4gb`), Caddy HTTPS, FastAPI/Next.js/Postgres/Redis/worker on app-vm, private TLS broker + digest-pinned runners on sandbox-vm
- **Inference:** Vultr Serverless Inference `deepseek-v4.1-flash-1017`
- **Containment:** no Docker socket on app services; broker-only runner lifecycle; fixture network egress allowlisted to the owned app IP :80/:443 with packet-proven denies for other destinations
- **E2E:** approve plan → broker run → PASS → container removed
- **Judge walkthrough:** [docs/DEMO.md](docs/DEMO.md)

## Vultr planning and plan review

Configure VULTR_INFERENCE_API_KEY and a VULTR_INFERENCE_MODEL present in the current Vultr catalog. The sole API gateway performs real HTTPS calls; the runner has no inference access. After target verification, use the project QA goal form to generate a draft, review its steps/scope and explicitly approve this version. Sensitive actions require individual consent and HIGH-risk approval; prohibited operations remain rejected. Policy changes invalidate prior approval.

    sh scripts/check-vultr-inference.sh

PowerShell equivalent:

    uv run --frozen --all-packages python scripts/check_vultr_inference.py

For a real generated plan persisted and approved through local HTTP:

    uv run --frozen --all-packages python scripts/authorization_smoke.py --generate-plan

See [ADR 0004](docs/adr/0004-vultr-planner.md) for retry/repair budgets, capability discovery, usage accounting, approval semantics and limits. Plans do not execute a browser until explicitly approved and dispatched to the broker.

## Owned QA demo fixture (Phase 10)

`tests/fixtures/site` is an owned FastAPI + SQLite subscription application for
deterministic local QA. It has synthetic `.test` registration, a fake local
mailbox, login, subscriptions, interval changes, cancellation, and a dashboard
count. It sends no email, SMS, or payment requests. Startup creates a new
random account password and private fixture-admin token in the ignored
`.cache/agentproof-demo/credentials.json`; do not publish that local file.
The fixture binds to loopback only.

```bash
make demo-seed
make demo-fixture-bug-on
make demo-run
# After the approved real run has completed:
make demo-reset DEMO_RUN_ID=<run-uuid>
make demo-fixture-bug-off
```

`BUG_MODE=on` is a seeded fixture defect: after cancellation, the stored
dashboard count remains stale. `BUG_MODE=off` recomputes it. Both modes have a
visible fixture-only banner. The reset endpoint requires the fixture's private
admin token and a single run UUID; it only deletes that run's
`AgentProof-E2E-<runid>-*` records. It is not an AgentProof production API.
`make demo-containment` reports the broker containment gate and never fakes a
successful metadata denial or cleanup event.

`demo-run` uses the real Vultr model gateway only and stops at explicit human
plan approval. It refuses offline planner output as a live demo. Live execution
requires `VULTR_INFERENCE_API_KEY`, `VULTR_INFERENCE_MODEL`, an administrative
demo allowlist entry, an authorized AgentProof account, and the approved plan.
Local demo defaults keep sandbox networking closed (`none`) unless an operator
configures an allowlisted fixture network with packet-tested egress. Do not
relax this to unrestricted Docker networking.

PowerShell equivalents use the same task runner:

```powershell
python scripts/manage.py demo-seed
python scripts/manage.py demo-fixture-bug-on
python scripts/manage.py demo-run
$env:DEMO_RUN_ID = '<run-uuid>'
python scripts/manage.py demo-reset
python scripts/manage.py demo-fixture-bug-off
python scripts/manage.py demo-containment
```

## Security and operational gates

Run `make quality-gate`, `make e2e`, `make containment`, `make restore-test`,
and `make security-audit` before evaluating a release. A skipped or blocked
sandbox gate is not a pass. Production Vultr uses an allowlisted fixture
network with packet-proven default-deny egress. Do not attach runners to
arbitrary Docker networks.

For an incident, set `EMERGENCY_STOP_ENABLED=true` in API/worker configuration
and restart them to block new admission, then run `make emergency-stop` from a
protected operator shell containing the broker URL and HMAC key. Check every
reported outcome and independently confirm that the broker Engine has no
AgentProof containers. The emergency stop remains latched until active leases
are gone and an operator calls the authenticated broker resume endpoint.
Disable public signup for private deployments with
`REGISTRATION_ENABLED=false`.

This implementation is not represented as fully production-ready for arbitrary
public multiuser browser targets; the Challenge 1 demo uses an owned fixture and
allowlisted egress only. See [docs/adr/0005-isolated-sandbox-execution.md](docs/adr/0005-isolated-sandbox-execution.md).
