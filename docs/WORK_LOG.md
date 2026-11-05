# Phase 01 work log

2026-11-05 — Contract reread. Workspace empty; no Git repository, manifests,
ADRs, phase report, tests, or applicable ancestor AGENTS.md. User authorized
Phase 01 implementation; no cloud provisioning or deployment authorized.

## Plan

1. Verify tool versions and official CLI help; scaffold Next.js and three uv apps.
2. Implement typed API health, settings, request IDs, errors, logging and migrations.
3. Build accessible web shells and generated OpenAPI/TypeScript contracts.
4. Add local Compose, nonroot images, portable commands, CI and operator docs.
5. Run frozen installs, static checks, tests, builds and local runtime probes;
   record actual results and precise blockers in PHASE_STATUS.md.

## Initial observations

- Node v26.5.0, Python 3.13.1, uv 0.8.3, Docker Compose v5.6.1,
  Git 2.53.0.windows.2 are installed.
- `corepack --version` fails because Corepack is absent. A pnpm npm shim exists;
  global installation changes are unnecessary and will not be made.
- Official Next.js CLI, Next.js 16 migration, uv workspaces, and Docker Compose
  startup-order documentation inspected. See DECISIONS.md for source links.
- No browser execution is permitted outside a broker-managed sandbox. Phase 01
  UI runtime verification will use HTTP; browser automation waits for that boundary.

## Implementation and validation observations

- Official Next.js CLI and uv initializers completed. shadcn CLI generated its
  configuration, button and utility; reviewed its changes, removed its generated
  remote Google font, and isolated custom theme tokens to preserve contrast.
- pnpm 11 moved non-registry settings out of .npmrc. Confirmed this in the official
  pnpm settings documentation; configured saveExact, engineStrict and storeDir in
  pnpm-workspace.yaml. All direct JS dependency versions are exact.
- A resolver conflict between arq and redis>=6 was fixed by selecting redis 5.3.1
  as the Python client. The actual Redis server is 7.4.5.
- Frozen Python install also passed in .cache/frozen-venv (52 packages). Frozen
  pnpm install passed while recreating node_modules with a new repository-local store.
- Existing unrelated services occupy 3000, 8000 and 5432. The initial AgentProof API
  bind on 8000 failed; a read-only health probe there returned 404 from the unrelated
  service and is not AgentProof evidence. Left those processes untouched. Verified
  AgentProof with web 13000, API 18000, PostgreSQL 15432 and Redis 6379 instead.
- Unit tests, schema generation, lint, strict typing, Next production build and
  all three Python package builds executed. Earlier lint/type issues were fixed.
- Local PostgreSQL and Redis health verified. Actual readiness returned 503 with
  dependencies down, then 200 when healthy. Stopped each owned dependency separately
  and verified outage/recovery with cleanup in finally. Alembic baseline applied.
- BuildKit --check passed for all four Dockerfiles; this is validation of Dockerfile
  syntax/base metadata, not proof of full application image builds.
- Read the Next dev-generated AGENTS.md and version-matched bundled guides for
  rewrites, layouts and errors. No browser launched, deployment or cloud resource
  creation performed. Final results and residual gates live in PHASE_STATUS.md.
