# Live demo (Challenge 1)

**URL:** https://agent.faizanmalik.me

AgentProof plans with Vultr Serverless Inference, then runs an approved browser
plan inside a disposable sandbox on a second Vultr VM.

## What judges should see

1. Open the app URL over HTTPS.
2. Sign in with the credentials provided by the team (not stored in this repo).
3. Open the project whose origin is `https://agent.faizanmalik.me` (owned fixture).
4. Generate a plan with a goal such as:

   > Open the owned fixture home page and confirm the heading AgentProof owned fixture is visible.

5. Approve the plan steps.
6. Start a run and wait until status is **completed** with verdict **PASS**.
7. Optional: open https://agent.faizanmalik.me/qa-fixture/ and confirm the heading
   `AgentProof owned fixture` is visible.

## Containment moment

Execution does not run in the app process. The sandbox broker on a separate VM
creates a nonroot, read-only, digest-pinned Playwright container on an allowlisted
network that may reach only the owned app IP on TCP 80/443; other egress is denied.
After the run, the container is removed.

## Architecture (short)

| Role | Responsibility |
| --- | --- |
| App VM | Caddy, Next.js, FastAPI, Postgres, Redis, worker |
| Sandbox VM | Private TLS broker + disposable runners |
| Vultr Serverless Inference | Plan generation only (runner has no model key) |

See [ADR 0005](adr/0005-isolated-sandbox-execution.md) and the README Vultr section
for boundaries. Operator deploy scripts and credentials stay outside the public tree.
