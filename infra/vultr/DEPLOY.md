# Two-VM production runbook

Operator runbook. Confirm cost ceiling and destroy window before provisioning.
No real hostname, instance, account plan or cost is asserted here.
Keep addresses, VM IDs, console screenshots and deployment evidence in an
access-controlled directory outside this repository. Redact private identifiers.

## Preflight and Ubuntu baseline

After exact cost approval, select Ubuntu 24.04 LTS if the account offers it, same
region and restricted VPC for both roles. Verify plan architecture matches built
images. Import an existing SSH public key; private keys stay outside the repo.
Create a sudo deploy account, verify key login in another connection before
disabling password/root login. Restrict SSH to the operator's observed IP in the
Vultr firewall and host firewall. Preserve console recovery.

Install Docker from its official signed apt repository, reviewing commands from
https://docs.docker.com/engine/install/ubuntu/ and matching the selected release.
Do not execute a remote installation shell script. Baseline package sequence:

```sh
sudo apt-get update
sudo apt-get install ca-certificates curl ufw unattended-upgrades
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$VERSION_CODENAME" "$(dpkg --print-architecture)" | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
apt-cache madison docker-ce
# Select reviewed available package versions; install docker-ce, docker-ce-cli,
# containerd.io, docker-buildx-plugin and docker-compose-plugin from that repo.
sudo ufw allow 22/tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
# Before deleting the broad rule, set OPERATOR_CIDR to the verified operator IP.
: "${OPERATOR_CIDR:?verified SSH source CIDR required}"
sudo ufw allow from "$OPERATOR_CIDR" to any port 22 proto tcp
sudo ufw delete allow 22/tcp
# app-vm only:
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status verbose
```

Consult https://docs.vultr.com/ for current firewall/VPC setup instructions.
Docker-published ports bypass normal UFW filtering: enforce provider firewall and
Docker DOCKER-USER rules, inspect IPv4/IPv6 paths, and externally probe both VMs.
See https://docs.docker.com/engine/network/packet-filtering-firewalls/.
Never publish Docker TCP (2375/2376), PostgreSQL, Redis, API or worker.

## Hostname, secrets and images

Confirm a domain controlled by the user; configure its A/AAAA to the app-vm only.
If no domain exists, obtain a real provider-supported hostname or controlled DNS
name with ACME validation before HTTPS verification. No fallback hostname has
been verified; this remains a gate. Do not claim successful placeholder curls.

Create `/etc/agentproof` owned by deploy, mode 700, and separate files mode 600:
`app.env`, `db.env`, `release.env`. Never print secret-bearing Compose config.
`release.env` contains absolute APP_ENV_FILE/DB_ENV_FILE paths, verified
APP_DOMAIN/ACME_EMAIL and WEB_IMAGE/API_IMAGE/WORKER_IMAGE immutable registry
digests. Authenticate the private registry using credential helpers or
password-stdin. No mutable latest tags. Pinned infrastructure versions in Compose
still require vulnerability review and registry availability checks before use.

`db.env` sets random POSTGRES_PASSWORD plus POSTGRES_USER/POSTGRES_DB.
`app.env` sets APP_ENV=production, DATABASE_URL using postgres:5432 and matching
URL-encoded database credentials, REDIS_URL=redis://redis:6379/0,
PUBLIC_APP_URL=https://the-verified-host, API_INTERNAL_URL=http://api:8000,
ARTIFACT_STORAGE_ROOT=/var/lib/agentproof/artifacts, independent random encryption
and signing keys, private HTTPS BROKER_URL and BROKER_HMAC_KEY. Configure trusted
broker CA through SSL_CERT_FILE and a read-only CA mount if using a private CA.
Set Vultr inference base URL/key/model only after catalog/subscription inspection.
Configure real SMTP, MAIL_BACKEND=smtp, sender and credentials for verification
emails; development mail must not be used in production. Keep
EMERGENCY_STOP_ENABLED=true and REGISTRATION_ENABLED=false until review completes.
Do not share backend secrets with web. API auth already uses secure production
cookies and same-origin checks; Caddy is the sole public ingress. API trusts
forwarding headers only within its unexposed, controlled container networks.

Build/test locally or CI from a reviewed revision:

```sh
pnpm install --frozen-lockfile
uv sync --frozen --project apps/api
pnpm --filter web build
docker build -f apps/web/Dockerfile -t "$WEB_TAG" .
docker build -f apps/api/Dockerfile -t "$API_TAG" .
docker build -f apps/worker/Dockerfile -t "$WORKER_TAG" .
```

Run project quality/security/integration gates, scan images, push private images
and record returned digests. Existing unrelated local changes must be reviewed
before any release. Use a manually dispatched protected GitHub environment for
future CI deployment; deployment automation has not been enabled.

## Owner action: publish target ownership proof (no auto-verify)

Project targets stay **UNVERIFIED** until a live proof fetch succeeds. Never mark
`authorized_origins` by hand and never reuse an expired challenge token.

For project origin `https://agent.faizanmalik.me` (HTTPS well-known method):

1. Sign in as a verified workspace admin on the production app.
2. Open the project → **Issue proof challenge** (method: HTTPS well-known file).
3. Copy the single-use token from the UI (also shown once in the API response).
4. On **app-vm**, publish exactly that token as a one-line text file:

```sh
# Replace TOKEN with the current challenge value from the UI (no quotes).
# Path matches Caddy: WELLKNOWN_DIR → /srv/well-known/agentproof-verification
: "${TOKEN:?paste the current challenge token}"
sudo install -d -m 0755 /var/lib/agentproof/well-known
printf '%s\n' "$TOKEN" | sudo tee /var/lib/agentproof/well-known/agentproof-verification >/dev/null
sudo chmod 0644 /var/lib/agentproof/well-known/agentproof-verification
# Confirm public fetch (expect HTTP 200 and the exact token body):
curl -fsS "https://agent.faizanmalik.me/.well-known/agentproof-verification"
```

5. In the UI, click **Verify published proof** before the challenge TTL (default 30
   minutes). On success the origin shows verified with an expiry.
6. If verify returns `PROOF_NOT_FOUND`, `PROOF_HTTP_ERROR`, `PROOF_FETCH_ERROR`,
   `CHALLENGE_EXPIRED`, or `CHALLENGE_REUSED`: fix the file or issue a **fresh**
   challenge and republish the new token. Do not keep an old token on disk.

DNS TXT alternative: create `_agentproof-verification.<host>` TXT with the exact
token, wait for propagation, then verify.

The public `/qa-fixture/` page is a separate static heading fixture (no forms).
Form/subscription demos use the local owned fixture under `tests/fixtures/site`
and require explicit owner approval before any public deploy of that app.

## Sandbox broker systemd unit (one-time)

On the sandbox VM, after `/etc/agentproof/broker.env` and
`/opt/agentproof/.venv-broker` exist:

```sh
sudo bash /opt/agentproof/infra/vultr/scripts/install_broker_service.sh
sudo bash /opt/agentproof/infra/vultr/scripts/ap.sh broker 50
```

`deploy_sandbox.sh` rebuilds the runner digest and restarts the unit; it does
**not** create the unit the first time. If restart fails with
`Unit agentproof-broker.service not found`, run the install script above.

## Sandbox boundary

Run broker as a dedicated host service with private TLS and HMAC, never public.
Allow its port only from the app-vm private source, matching IPv4/IPv6 provider
and host ACLs. Do not mount docker.sock in any app container. Broker's host account
controls only sandbox Engine; Engine access is root-equivalent. Install locked
broker dependencies in a host venv, use a restart-on-failure systemd service,
protected env file and persistent private broker state/artifacts. Private registry
runner image must be pre-pulled and configured by SHA-256 digest.

Safe default remains `BROKER_SANDBOX_NETWORK=none`. For the Challenge 1 owned-fixture
demo, set an allowlisted fixture network only after host `DOCKER-USER` rules and
packet-level negatives pass (deny public HTTPS/metadata/non-allowlisted ports;
allow owned app :80/:443). Enable the compose `execution` worker profile only with
matching broker TLS/HMAC and a digest-pinned runner image. Browser route hooks alone
are insufficient. gVisor is not proven. A separate VM alone does not establish safe
public multi-tenant execution on a shared host kernel. See infra/sandbox/README.md
and docs/adr/0005-isolated-sandbox-execution.md.

## Deploy and migration

Run from repository root on app-vm after configuring all real values.
Install the short compose wrapper once (hides `--env-file` / `-f`):

```sh
sudo bash /opt/agentproof/infra/vultr/scripts/install_apc.sh
```

```sh
set -eu
apc --profile maintenance --profile execution config --quiet
apc --profile maintenance --profile execution pull
apc up -d postgres redis
apc run --rm artifacts-init
# Capture encrypted pre-migration backup first for an existing deployment.
# Alembic uses PostgreSQL transactional DDL. Serialize deployments externally.
apc run --rm migrate
apc up -d --remove-orphans
```

Worker is intentionally behind the execution profile. After containment, recovery
and tenant gates pass, change emergency stop/registration deliberately and run
`apc --profile execution up -d --remove-orphans`. Startup dependencies are not a
substitute for a successful migration or external checks. Size VM from measured
memory/disk rather than raising caps blindly. Authenticated backend broker
artifact import supports large evidence transfers privately; there is no public
upload/download directory. Public API body limit is 2 MB and SSE flush is immediate.

### Routine code updates (git-deployed VMs)

After pushing to the deploy remote, refresh each VM with one script instead of
hand-running long `docker build` / compose sequences:

```sh
# app-vm
sudo bash /opt/agentproof/infra/vultr/scripts/deploy_app.sh

# sandbox-vm (when runner/broker changed)
sudo bash /opt/agentproof/infra/vultr/scripts/deploy_sandbox.sh
```

Use `--no-pull` if you already pulled. Scripts rebuild local images, refresh
digest pins in `/etc/agentproof/release.env` / `broker.env`, migrate, and restart.

For logs and common ops, use `infra/vultr/scripts/ap.sh` instead of long
`docker compose --env-file ... logs` / `journalctl` lines:

```sh
AP=/opt/agentproof/infra/vultr/scripts/ap.sh
sudo bash $AP logs            # follow
sudo bash $AP tail worker 100
sudo bash $AP mail
sudo bash $AP broker -f       # sandbox VM
sudo bash $AP health
```

## Backups, rollback and operations

Before migration, stop new admissions and drain/stop workers. Back up PostgreSQL
with `pg_dump -Fc` inside postgres, capturing to a mode-600 file on an encrypted
volume outside the repo. Also back up private artifact volume, broker SQLite state
using SQLite backup API or while broker stopped, and deployment keys/configs via
an encrypted secret backup. Redis AOF is durable queue assistance; PostgreSQL is
the authority. Keep evidence and DB copies consistent by quiescing writers.
Back up Caddy state to retain certificates. Define retention and priced off-VM
encrypted copy destination before enabling scheduled backups.

Record previous image digests and exact Alembic revision. Prefer additive
migrations compatible with both versions. To roll back code, stop worker, restore
previous image references and recreate API/web/worker after schema compatibility
review. A downgrade job uses `apc run --rm migrate alembic -c apps/api/alembic.ini
downgrade "$PREVIOUS_REVISION"`; do not downgrade production unless that exact
transition was rehearsed against restored disposable data and data loss approved.
For incompatible schemas restore pre-migration DB plus matching artifacts into
replacement private volumes, verify, then switch; do not erase current volumes.
Rehearse backup/restore, up/down/up and image rollback on disposable infrastructure
before release. No rehearsal is claimed here.

JSON container logs rotate at 10 MB x 3 per service. Configure host journald
retention, disk/inode alerts, dependency and worker heartbeat checks, HTTPS expiry,
API readiness, sandbox orphan inventory and billing alerts to an approved private
destination. No external alert or scheduled backup is currently configured.

## Acceptance evidence (all pending live infrastructure)

- External browser: HTTPS register/login, workspace, ownership proof, plan from
  live Vultr inference, approved sandbox run, private report download, logout.
- Prove response.model exactly equals approved configured model; retain redacted
  usage and provider-failure evidence. Use scripts/check_vultr_inference.py after
  cost/quota approval; live calls may incur charges.
- Verify UI/API and sandbox on separate Vultr VMs using private redacted console
  evidence. With real PUBLIC_APP_URL set, curl its `/api/v1/health/live`,
  `/api/v1/health/ready`, and `/health`; no placeholder request counts.
- Inspect `ss -tulpn`, provider/host/Docker rules and independent external probes:
  only source-restricted SSH plus app HTTP/HTTPS; no DB/Redis/API/broker/Engine.
- Kill a sandbox and prove cleanup; restart broker/worker and prove recovery;
  verify cross-tenant artifact/run denial and SSE replay/revocation.
- Restore a backup and compare schema/data/artifact hashes; demonstrate monitoring
  delivery, log rotation, alert thresholds and post-test spend audit.

Do not mark DEPLOYED until the public URL loads and a complete real run finishes.
