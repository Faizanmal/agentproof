# Phase

Phase 01 — Repository foundation. **PARTIAL** (2026-11-05–06, Asia/Calcutta).

The implementation and functional checks pass on documented alternate loopback
ports. The exact default-port acceptance check remains BLOCKED: unrelated existing
services own 3000, 8000 and 5432. No unrelated process was stopped. No later phase
has started.

# implemented files

- Root: pnpm/uv workspaces and lockfiles, exact JS/toolchain versions, Makefile,
  portable task scripts, env example, ignore rules, README and MIT license.
- API: app factory/lifespan, Pydantic Settings, bounded Postgres/Redis probes,
  typed health/version/problem models, request IDs, JSON logging, generic errors,
  production fixture-credential guard and Alembic baseline.
- Web: official Next.js scaffold, landing/auth/project shells, real readiness
  check, same-origin rewrite, light/dark support, loading/404/error views,
  shadcn configuration/button and Lucide icons. Auth is labelled not connected.
- Contracts: generated OpenAPI JSON, generated TypeScript schemas and typed
  same-origin client; exporter/freshness check.
- Infra: local Compose, inert production placeholder, Caddy example, four nonroot
  Dockerfiles, sandbox/Vultr boundary docs. No execution broker or runner enabled.
- Validation: unit tests, HTTP smoke/evidence capture, secret scanner, safe CI
  workflow, ADR 0001, DECISIONS and WORK_LOG.
- Real HTTP evidence: [runtime.json](evidence/phase01/runtime.json).

# commands executed

Commands below ran from the agentproof repository root unless noted. On Windows,
PNPM uses pnpm.cmd; portable scripts select that shim automatically.

| Command / operation                                                                                                  | Real result                                                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| node --version; corepack --version; python --version; docker compose version; git status                             | Node 26.5.0, Python 3.13.1, Compose 5.6.1. Corepack absent. Initially no Git repository; initialized this empty repository.                  |
| pnpm --version; uv --version; git --version; docker info                                                             | pnpm 11.18.0, uv 0.8.3, Git 2.53.0.windows.2, Docker Engine 29.9.2. Docker access required reviewed local permission.                        |
| pnpm create next-app@latest --help; create apps/web with TS/Tailwind/ESLint/App Router/src-dir/pnpm/skip-install/yes | PASS. Official scaffold selected Next 16.4.5 and React 19.3.0.                                                                               |
| uv init apps/api, apps/worker, apps/runner --app --python 3.13 --vcs none                                            | PASS after setting UV_CACHE_DIR to .cache/uv.                                                                                                |
| pnpm dlx shadcn@latest init --help; init --defaults --no-monorepo --cwd apps/web --yes                               | PASS. CLI 4.23.0 generated components/config; reviewed and removed generated remote font.                                                    |
| uv lock; uv sync --frozen --all-packages                                                                             | PASS after correcting arq/Redis-client compatibility; 52 installed packages.                                                                 |
| UV_PROJECT_ENVIRONMENT=.cache/frozen-venv uv sync --frozen --all-packages                                            | PASS in a new environment; 52 packages installed.                                                                                            |
| pnpm install --lockfile-only; pnpm install --force --frozen-lockfile                                                 | PASS while recreating node_modules and populating a fresh repository-local store.                                                            |
| python scripts/manage.py bootstrap                                                                                   | PASS: final frozen pnpm install, frozen Python sync, local env copy without overwriting existing env.                                        |
| docker compose -f infra/compose.dev.yml config --quiet                                                               | PASS. Default ports resolve to loopback-only bindings.                                                                                       |
| docker compose -f infra/compose.prod.yml config --quiet                                                              | PASS; no services defined. Not a deployment configuration.                                                                                   |
| POSTGRES_PORT=15432 docker compose -f infra/compose.dev.yml up -d --wait postgres redis                              | PASS. Both healthy; ports 15432 and 6379 bound only to 127.0.0.1.                                                                            |
| API start on 127.0.0.1:8000                                                                                          | BLOCKED: Windows address already in use by an unrelated application. Initial curl there returned its 404; excluded from AgentProof evidence. |
| API start on 127.0.0.1:18000; curl.exe -fsS .../api/v1/health/live                                                   | PASS, HTTP 200 and status alive.                                                                                                             |
| curl.exe .../api/v1/health/ready                                                                                     | Observed 503 with both dependencies down, then 200 with both up.                                                                             |
| DATABASE_URL pointing to owned port 15432; python scripts/manage.py migrate                                          | PASS. PostgreSQL query confirmed alembic_version = 0001_baseline.                                                                            |
| pnpm --filter web dev --hostname 127.0.0.1 --port 13000                                                              | PASS. Real HTTP pages rendered; no browser launched.                                                                                         |
| uv run --frozen --all-packages python scripts/smoke.py --api-port 18000 --web-port 13000                             | PASS: UI pages, 404, live/ready/version/errors, request IDs, same-origin rewrite.                                                            |
| Stop owned Redis, smoke --expect-not-ready, restore in finally; repeat for owned PostgreSQL; final smoke             | PASS for both individual dependency outages and recovery. Both containers healthy afterward.                                                 |
| python scripts/manage.py contracts; ... test; ... typecheck; ... security                                            | Executed; final totals below. Generated OpenAPI and TS types are committed inputs.                                                           |
| Ruff check/format-check; mypy; ESLint; TS; Prettier; pnpm peers check                                                | PASS after correcting initial lint/type findings. No peer issues.                                                                            |
| pnpm --filter web build                                                                                              | PASS: optimized production build; /, /auth, /projects and not-found output generated.                                                        |
| uv build --package agentproof-api/worker/runner --out-dir .cache/dist                                                | PASS: wheel and sdist for each of three packages.                                                                                            |
| docker build --check -f apps/{api,worker,runner,web}/Dockerfile .                                                    | All four PASS, no warnings. Base image metadata resolved. These are not full image builds.                                                   |

# test totals/results

- Unit tests: **24 passed**, 0 failed, 1 upstream deprecation warning (7.84 seconds).
- Ruff and format-check: pass; strict mypy includes 19 source files, including migrations.
- Frontend: ESLint, strict TypeScript, Prettier and peer checks pass.
- CI YAML parses and safe permissions/Compose/socket restrictions are asserted by unit tests.
- Secret scan passes on tracked and nonignored candidate source files. Generated
  lockfiles and binary assets are excluded; only explicit public fixture lines are allowlisted.
- HTTP smoke passes with real dependencies, each individual dependency down,
  and recovery. Evidence is HTTP-only; no screenshots or browser results exist.
- Production Next and Python distribution builds pass. Full application Docker
  image builds and a hosted GitHub Actions run have not been performed.
- One upstream deprecation warning: Starlette TestClient currently warns about
  httpx compatibility. Tests still pass; no warning is hidden.

Acceptance gates: 1 PASS; 2 PASS with documented database host-port override;
3 PARTIAL (alternate port PASS, exact localhost:8000 BLOCKED); 4 PASS using the
alternate dev port; 5 PASS; 6 PASS; 7 PASS. Overall phase remains PARTIAL.

# real external dependencies verified

- npm/PyPI dependencies actually resolved/downloaded and locked. Frozen installs verified.
- PostgreSQL 16.11 and Redis 7.4.5 containers actually ran with healthy status;
  SQL SELECT 1, Redis PING, migration and outage/recovery checks succeeded.
- Docker metadata resolved Python 3.13.1, Node 26.5.0 and uv 0.8.3 base image tags.
- Official Next.js, uv, pnpm and Docker docs inspected; Next version-matched bundled
  docs reviewed after Next generated its AGENTS.md.
- Vultr documentation confirms the configured inference base URL and /v1/models.
  Real model discovery/completions remain BLOCKED: no inference key/model configured.
  No inference call or provider substitution occurred.

# security observations

- No runtime socket, privileged mode, host network or host mounts in application
  Compose definitions. App runtime images declare nonroot users. No active runner service.
- Dependency host ports use loopback bindings. Public local-only database fixtures
  are documented and specifically annotated for the scanner; production Settings
  reject that fixture password.
- Settings mask secrets and hide validation inputs. Errors exclude raw validation
  data and exception details; request logs exclude raw paths, queries, bodies and headers.
- No credentials are collected by the auth placeholder. Authentication, CSRF,
  tenancy, secret encryption, broker isolation and run budgets are future gates.
- Dockerfiles use version tags, not immutable digests. No vulnerability audit or
  production isolation claim is made. Production Compose is inert.

Verification cleanup: the owned dev supervisor and Compose containers were stopped
after testing. Named database volumes were retained; unrelated applications were
left untouched. The documented dev supervisor also passed HTTP smoke checks.

# remaining blockers

- Exact default-port runtime check: free ports 3000/8000/5432, or explicitly accept
  documented alternatives. Existing unrelated applications were left untouched.
- Vultr external verification: securely configure VULTR_INFERENCE_API_KEY and
  select/verify a real model in its later phase; no cloud provisioning authorized.
- Browser/visual QA requires a disposable broker-managed sandbox. It was not
  bypassed with a host browser. Full image builds and hosted CI remain unverified.

# next phase

No next-phase implementation started. Resolve or explicitly accept the default-port
verification deviation before progressing. Subsequent authorization/project work
requires its own prompt and contract/gate review. Production deployment remains unauthorized.
