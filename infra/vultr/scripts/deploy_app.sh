#!/usr/bin/env bash
# App VM: git pull → rebuild local images → migrate → recreate services.
# Usage (on app VM):
#   sudo bash /opt/agentproof/infra/vultr/scripts/deploy_app.sh
#   sudo bash /opt/agentproof/infra/vultr/scripts/deploy_app.sh --no-pull
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RELEASE_ENV="${RELEASE_ENV:-/etc/agentproof/release.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/infra/compose.prod.yml}"
DO_PULL=1

for arg in "$@"; do
  case "$arg" in
    --no-pull) DO_PULL=0 ;;
    -h|--help)
      echo "Usage: $0 [--no-pull]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$RELEASE_ENV" ]]; then
  echo "Missing $RELEASE_ENV" >&2
  exit 1
fi

cd "$ROOT"
if [[ "$DO_PULL" -eq 1 ]]; then
  git pull --ff-only
fi
echo "commit=$(git rev-parse --short HEAD)"

bash "$ROOT/infra/vultr/scripts/install_dc.sh" >/dev/null || true

dc() {
  bash "$ROOT/infra/vultr/scripts/dc.sh" "$@"
}

echo "==> build api / worker / web (parallel)"
docker build -f apps/api/Dockerfile -t agentproof-api:local "$ROOT" &
docker build -f apps/worker/Dockerfile -t agentproof-worker:local "$ROOT" &
docker build -f apps/web/Dockerfile -t agentproof-web:local "$ROOT" &
wait

# If release.env pins image IDs, refresh them to the new local builds.
python3 - <<'PY' "$RELEASE_ENV"
from pathlib import Path
import subprocess
import sys

path = Path(sys.argv[1])
text = path.read_text()
mapping = {
    "API_IMAGE": "agentproof-api:local",
    "WORKER_IMAGE": "agentproof-worker:local",
    "WEB_IMAGE": "agentproof-web:local",
}
lines = []
changed = False
for line in text.splitlines():
    if "=" not in line or line.lstrip().startswith("#"):
        lines.append(line)
        continue
    key, _, _val = line.partition("=")
    if key in mapping:
        image_id = subprocess.check_output(
            ["docker", "image", "inspect", mapping[key], "--format", "{{.Id}}"],
            text=True,
        ).strip()
        new = f"{key}={image_id}"
        changed = changed or new != line
        lines.append(new)
    else:
        lines.append(line)
if changed:
    path.write_text("\n".join(lines) + "\n")
    print(f"updated image digests in {path}")
else:
    print(f"left {path} unchanged (already using expected tags or no IMAGE keys)")
PY

echo "==> migrate"
dc --profile maintenance run --rm migrate

echo "==> recreate app services"
dc --profile execution up -d --force-recreate api web worker caddy

APP_DOMAIN="$(grep -E '^APP_DOMAIN=' "$RELEASE_ENV" | cut -d= -f2- || true)"
echo "==> status"
dc --profile execution ps
if [[ -n "$APP_DOMAIN" ]]; then
  curl -fsS "https://${APP_DOMAIN}/api/v1/health/live" || true
  echo
fi
echo "DONE app deploy"
