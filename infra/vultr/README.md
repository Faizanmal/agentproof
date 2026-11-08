# Vultr release boundary

Production preparation: [runbook](DEPLOY.md), [release.env example](release.env.example).
Live Challenge 1 demo: https://agent.faizanmalik.me. Image publish is manually gated via
`.github/workflows/deploy.yml` (`workflow_dispatch` + `PUBLISH`).

Frontend, API and sandbox VM deployment use Vultr. Discover model IDs from the real
inference `/v1/models` response; verify the selected model against the completion
response. Never substitute another inference provider. Keep inference keys in
backend/worker configuration only. Judge walkthrough: [docs/DEMO.md](../../docs/DEMO.md).
