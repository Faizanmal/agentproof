# AgentProof

A local foundation for authorized browser QA with evidence-derived findings.
Phase 01 provides a web shell, API health contracts, local data services and
reproducible checks. Authentication is labelled **not connected**; project
creation, inference and browser execution are disabled.

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
starting services. Its PostgreSQL password is a deliberately public local-only
fixture, and inference/signing/encryption keys are empty. Do not reuse these
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

| Target      | Action                                                                       |
| ----------- | ---------------------------------------------------------------------------- |
| `bootstrap` | Frozen pnpm and uv workspace installation; create local env if absent        |
| `dev`       | Start loopback-only API and Next dev servers; stop both on exit              |
| `db-up`     | Start PostgreSQL/Redis and wait for health checks                            |
| `migrate`   | Apply Alembic revisions (initial empty baseline)                             |
| `test`      | Unit tests and generated OpenAPI freshness check                             |
| `lint`      | Ruff, Python formatting, ESLint and Prettier checks                          |
| `typecheck` | Strict mypy, Next type generation and strict TypeScript                      |
| `build`     | Production Next build and three Python distributions                         |
| `smoke`     | Local HTTP endpoints, UI pages and same-origin proxy; requires both services |
| `contracts` | Export FastAPI OpenAPI and regenerate TS types                               |
| `security`  | detect-secrets on tracked and nonignored candidate source files              |
| `format`    | Apply Ruff and Prettier formatting                                           |

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
                                              | PostgreSQL :5432
                                              | Redis :6379
Future: Vultr Caddy TLS -> Next.js (same routing strategy)
Future: worker -> private broker -> disposable isolated runner
```

Next.js owns all API rewrites. Caddy's future configuration forwards the whole
origin to Next.js; no second API routing mechanism is configured. See
[ADR 0001](docs/adr/0001-architecture.md). Contracts are generated from FastAPI;
the browser uses a typed same-origin client and holds no API credentials.

| Port | Purpose       | Local binding |
| ---- | ------------- | ------------- |
| 3000 | Next.js       | Loopback      |
| 8000 | FastAPI       | Loopback      |
| 5432 | PostgreSQL 16 | `127.0.0.1`   |
| 6379 | Redis 7       | `127.0.0.1`   |

API, worker and runner Dockerfiles use nonroot runtime users and frozen installs.
The runner image is a packaging scaffold without Chromium. No app has a container
runtime socket. `infra/compose.prod.yml` is intentionally inert and must not be
used as a deployment configuration. No cloud resources have been provisioned.

Optional containerized application path:

```bash
docker compose -f infra/compose.dev.yml --profile app up -d --build --wait
docker compose -f infra/compose.dev.yml exec api alembic -c apps/api/alembic.ini upgrade head
```

Stop host dev servers first to avoid port conflicts. The standalone web image
builds its rewrite for the private `api:8000` origin; changing that origin requires
a rebuild. This is local development infrastructure, not production isolation.

## Troubleshooting and cleanup

- Docker permission/pipe errors: ensure Docker Desktop is running with Linux
  containers and the current user/process has permission. Check `docker info`.
- `connection refused` during install: package registry access is required on first
  install. Cached dependencies may work offline; do not manufacture lockfiles.
- Readiness 503: inspect `docker compose -f infra/compose.dev.yml ps` and confirm
  `DATABASE_URL`/`REDIS_URL` match the selected host or container environment.
  Liveness stays 200 even when either dependency is unavailable.
- Port conflicts: leave unrelated applications running and select explicit loopback
  alternatives. For example in PowerShell:

  ```powershell
  $env:POSTGRES_PORT = "15432"
  $env:DATABASE_URL = "postgresql+asyncpg://agentproof:agentproof_local_only@127.0.0.1:15432/agentproof" # pragma: allowlist secret -- public local-only fixture
  python scripts/manage.py db-up
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

Browser execution is restricted to later broker-managed disposable sandboxes.
No host browser tests are included. See [sandbox requirements](infra/sandbox/README.md),
[decisions](docs/DECISIONS.md), [work log](docs/WORK_LOG.md) and
[actual phase status](docs/PHASE_STATUS.md). No production security claim follows
from successful local Docker operation.
