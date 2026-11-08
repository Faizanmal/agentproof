# Vultr release boundary

Production preparation: [runbook](DEPLOY.md), [release.env example](release.env.example).
Live Challenge 1 demo: https://agent.faizanmalik.me. Image publish is manually gated via
`.github/workflows/deploy.yml` (`workflow_dispatch` + `PUBLISH`).

## After `git pull` (update VMs)

On **app VM** (repo root usually `/opt/agentproof`):

```sh
sudo bash infra/vultr/scripts/deploy_app.sh
```

On **sandbox VM**:

```sh
sudo bash infra/vultr/scripts/deploy_sandbox.sh
```

Each script pulls (unless `--no-pull`), rebuilds the needed local images, refreshes digests in `/etc/agentproof/*.env`, migrates / restarts, and prints health. Skip the long manual `docker build` / `compose` sequence.

### Why compose flags are long (and how to drop them)

`release.env` lives under `/etc/agentproof` (secrets + image digests, not in git)
and the file is `infra/compose.prod.yml`, not the default `compose.yml` in cwd —
so raw Docker needs `--env-file` and `-f`. Install the short wrapper once on the
**app VM**:

```sh
sudo bash /opt/agentproof/infra/vultr/scripts/install_dc.sh
# (also done automatically by deploy_app.sh / ap.sh)
```

Then use:

```sh
dc ps
dc logs -f worker
dc --profile execution up -d
dc exec -T api python -m agentproof_api.cli deliver-mail
```

### Logs / ops (short)

```sh
AP=/opt/agentproof/infra/vultr/scripts/ap.sh

sudo bash $AP logs              # follow api worker web caddy
sudo bash $AP logs worker       # one service
sudo bash $AP tail api 120      # last 120 lines, no follow
sudo bash $AP ps
sudo bash $AP mail              # deliver-mail once
sudo bash $AP broker            # sandbox journal (last 80)
sudo bash $AP broker -f         # sandbox follow
sudo bash $AP health
```

Frontend, API and sandbox VM deployment use Vultr. Discover model IDs from the real
inference `/v1/models` response; verify the selected model against the completion
response. Never substitute another inference provider. Keep inference keys in
backend/worker configuration only. Judge walkthrough: [docs/DEMO.md](../../docs/DEMO.md).
