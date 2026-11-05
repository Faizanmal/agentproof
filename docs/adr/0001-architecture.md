# ADR 0001: Local foundation and trust boundaries

Date: 2026-11-05
Status: Accepted for Phase 01; production deployment is not authorized.

## Context

AgentProof will turn an authorized QA goal into a reviewed typed plan, isolated
browser execution and evidence-derived findings. Phase 01 establishes packaging,
health contracts, a UI shell and reproducible local checks. Authentication,
projects, inference, browser execution and containment are later gated work.

## Decision

- Use a pnpm monorepo for Next.js 16/React 19 and generated TypeScript contracts.
  Use a uv workspace with separate API, worker and runner packages, a shared
  lockfile and explicit package selection in production images.
- FastAPI owns `/api/v1/*`. Pydantic models produce the committed OpenAPI document;
  openapi-typescript derives TS types and openapi-fetch supplies a typed client.
- **Next.js rewrites are the sole API routing strategy**, in development and
  production. The browser calls relative `/api/v1/*` URLs. `API_INTERNAL_URL` is
  a server-only operator setting used when Next.js evaluates configuration.
  Caddy will terminate TLS on Vultr and proxy the entire origin to Next.js;
  it must not add a second `/api` path-routing rule. No permissive CORS is enabled.
- PostgreSQL 16 and Redis 7 use named local volumes and loopback-only host ports.
  Local credentials are public development fixtures. Production Compose remains
  empty until secrets, TLS, private networking and deployment are reviewed.
- `/health/live` never connects to dependencies. `/health/ready` executes
  `SELECT 1` and Redis `PING` concurrently with bounded timeouts; either failure
  yields HTTP 503. Readiness can recover without a process restart.
- Errors use RFC 7807-style `application/problem+json` with a server-generated
  `request_id`, also sent in `X-Request-ID`. Health and API responses use no-store.
  Logs contain UTC timestamps, static event names, route templates, method,
  response status and duration; no headers, query values, bodies or exception text.
- Alembic manages schema evolution. The initial revision is an empty baseline;
  no production `create_all` path exists.
- Worker and runner fail closed without `--check`. The runner has no HTTP server,
  browser binaries, execution path, secret configuration or application Compose
  service in Phase 01. Browser automation awaits a private broker-controlled
  disposable sandbox. HTTP smoke checks do not launch a browser.

## Future security gates

The browser and all target-derived content are untrusted. Backend-side tenancy,
cookie security, CSRF, rotating refresh tokens, Argon2id, secret encryption,
deterministic action policies, signed jobs, leases, cancellation, audit events,
artifact controls and budget enforcement must precede the relevant functionality.
Only the broker may control sandbox creation; API, worker and runner must never
receive a runtime socket. Dedicated Vultr sandbox VMs and controlled egress are
required before claiming production tenant isolation. Test-account credentials may
enter one ephemeral login step, never model prompts or durable evidence.

## Consequences

The frontend server is on the API request path; proxy availability and headers
will need testing with authentication. Changing the internal API origin requires
rebuilding the standalone web image. A shared Python lock constrains dependency
compatibility, verified by `uv lock` and frozen workspace installation. No
production readiness or successful browser journey is claimed by this ADR.
