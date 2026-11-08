# ADR 0005: Isolated broker-managed browser execution

## Status

**Accepted for Challenge 1 demo (2026-11-08).** Broker + constrained runner run on a
dedicated Vultr sandbox VM. End-to-end approve → dispatch → Playwright → teardown
has passed against an owned HTTPS fixture. Fixture-network egress has packet-proven
default-deny negatives. This is **not** a claim of multi-tenant public-target
isolation or a full origin-aware TLS/DNS pinning gateway for arbitrary sites.

## Decision

Only a private broker process controls the dedicated sandbox Docker Engine. API,
web, worker, inference gateway and runner do not receive its socket. The broker
accepts timestamped HMAC-authenticated short-lived jobs, validates replay, digest,
origin/path and resource constraints, assigns an opaque sandbox ID, starts a single
disposable immutable-digest runner container, and reconciles/removes it on
completion, timeout, cancellation or restart. The runner accepts QAPlanV1 only and
has no shell/script execution or inference/database secrets.

The browser runs as a nonroot user in a read-only container with dropped
capabilities, no-new-privileges, Docker's default seccomp, bounded tmpfs/shared
memory, cgroup CPU/memory/PID limits and a hard duration. Runner events are
allowlisted and replayable. Plan paths/actions and request origins are checked
again in the runner. Runner packages must be installed non-editable into the
image `site-packages` (runtime copies only `.venv`).

Production Challenge 1 uses a separate Vultr sandbox VM from the app VM.

## Network model (demo vs default)

- **Safe default:** `BROKER_SANDBOX_NETWORK=none` (no target connectivity).
- **Challenge 1 demo:** allowlisted Docker network `agentproof_fixture_net` only,
  gated by `BROKER_SANDBOX_NETWORK_ALLOWLIST`. Host firewall (`DOCKER-USER`) accepts
  TCP 80/443 to the owned app IP and drops other egress from that bridge
  (verified: public HTTPS, link-local metadata, alternate public IPs, non-http
  ports denied; owned fixture allowed).
- Playwright route/DNS guards remain defense in depth. They do **not** replace
  the host egress policy.
- Arbitrary public multi-origin browsing still requires a stronger origin-aware
  gateway (connection-time DNS/IP pin, SNI/Host/redirect enforcement, WebRTC/raw
  socket coverage) before advertising unrestricted targets.

## Proven on the demo path

- Worker claims approved runs, signs broker jobs, polls events, imports artifacts.
- Runner launches Chromium headless, navigates the owned fixture, asserts visibility,
  emits step/artifact/completion events.
- Broker cancels leases, rejects nonce replay and oversize artifact limits, removes
  managed containers after completion (`sandbox.cleaned_up`).

## Remaining limits

- One-use TEST-credential delivery into the runner login path is not required for
  the owned fixture demo and is not claimed complete for credentialed sites.
- gVisor/rootless Docker are not claimed; evaluate before multi-tenant production.
- Local Docker Desktop is not sufficient proof of the Vultr containment story.
