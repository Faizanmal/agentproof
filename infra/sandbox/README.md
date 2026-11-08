# Sandbox broker and containment boundary

## Components

- `apps/broker` is a private FastAPI control plane and the only component that invokes the Docker CLI. Run it as a host service on a dedicated Linux sandbox VM, with access only to that VM's dedicated Docker Engine. Do not put the Docker socket in the API, worker, web, runner, or application compose files. The broker is not included in `infra/compose.dev.yml`.
- `apps/runner` is one short-lived container. It receives a strict signed-manifest payload (bind-mounted read-only), executes typed QAPlanV1 with Playwright Chromium, prints bounded redacted JSON events, and exits.
- API/worker production traffic uses private HTTPS to the broker with HMAC over `timestamp + "." + method + "." + path-and-query + "." + body` (30s skew). Job envelopes also carry a job signature, single-use nonce, approved origins, plan digest, and limits. Duplicate identical job IDs are idempotent; changed payloads and reused nonces are rejected.
- Broker IDs are opaque `sbx_...` values. Cancellation kills/removes the mapped container. Restart reconciliation kills leftover broker-labeled containers. Events are persisted with a field allowlist and replay IDs.

## Container policy

The broker uses an operator-configured immutable image digest and `--pull=never`. It does not accept image, command, privileged mode, host mounts, environment, devices or networking from an API caller or plan. The runner is nonroot UID 10001, read-only root filesystem, no capabilities, no-new-privileges, default Docker seccomp, capped PIDs/CPU/memory, 128 MiB shared memory, and 64 MiB noexec/nosuid/nodev tmpfs. No Docker-in-Docker or Docker socket exists in the runner.

gVisor is not claimed. Prefer a separate Vultr sandbox VM. Local Docker Desktop is not multi-tenant isolation proof.

## Network isolation

- **Safe default:** `BROKER_SANDBOX_NETWORK=none`.
- **Challenge 1 demo:** allowlisted `agentproof_fixture_net` only (`BROKER_SANDBOX_NETWORK_ALLOWLIST`). Host `DOCKER-USER` rules accept TCP 80/443 to the owned app IP and drop other bridge egress. Packet negatives for public HTTPS, link-local metadata, alternate public IPs, and non-http ports have passed on the sandbox VM.
- Playwright route/DNS guards are defense in depth only. They do not replace host egress policy.
- Arbitrary public multi-origin browsing still needs a stronger origin-aware gateway before advertising unrestricted targets. See [ADR 0005](../../docs/adr/0005-isolated-sandbox-execution.md).

## Execution path

The worker claims approved runs, creates signed broker jobs, polls sandbox events, imports artifacts, and finishes the durable run record. Keep public signup and emergency-stop policy under operator control for private deployments.

## Local commands (Linux sandbox host)

1. Build the runner: `docker build -f apps/runner/Dockerfile -t agentproof-runner:local .`
2. Pin `BROKER_RUNNER_IMAGE_DIGEST` to the image id/digest (`--pull=never`).
3. Generate a private HMAC key; share only with API/worker/broker secret stores.
4. Leave network at `none` until fixture allowlist + packet tests are in place.
5. Start: `uv run --package agentproof-broker agentproof-broker`

## Chromium executor test stage

```sh
docker build --target browser-test -f apps/runner/Dockerfile -t agentproof-runner-browser-test .
docker run --rm agentproof-runner-browser-test
```

A successful browser-test run does not by itself prove host egress isolation.
