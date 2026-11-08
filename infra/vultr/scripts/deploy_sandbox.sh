#!/usr/bin/env bash
# Sandbox VM: git pull → rebuild runner → refresh broker digest → restart broker.
# Usage (on sandbox VM):
#   sudo bash /opt/agentproof/infra/vultr/scripts/deploy_sandbox.sh
#   sudo bash /opt/agentproof/infra/vultr/scripts/deploy_sandbox.sh --no-pull
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
BROKER_ENV="${BROKER_ENV:-/etc/agentproof/broker.env}"
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

if [[ ! -f "$BROKER_ENV" ]]; then
  echo "Missing $BROKER_ENV" >&2
  exit 1
fi

cd "$ROOT"
if [[ "$DO_PULL" -eq 1 ]]; then
  git pull --ff-only
fi
echo "commit=$(git rev-parse --short HEAD)"

echo "==> build runner (runtime target only; skip browser-test stage)"
DOCKER_BUILDKIT=1 docker build --target runtime -f apps/runner/Dockerfile -t agentproof-runner:local "$ROOT"
IMAGE_ID="$(docker image inspect agentproof-runner:local --format '{{.Id}}')"
echo "runner=$IMAGE_ID"

python3 - <<'PY' "$BROKER_ENV" "$IMAGE_ID"
from pathlib import Path
import sys

path = Path(sys.argv[1])
image_id = sys.argv[2]
lines = [
    line
    for line in path.read_text().splitlines()
    if not line.startswith("BROKER_RUNNER_IMAGE_DIGEST=")
]
lines.append(f"BROKER_RUNNER_IMAGE_DIGEST={image_id}")
path.write_text("\n".join(lines) + "\n")
print(f"updated BROKER_RUNNER_IMAGE_DIGEST in {path}")
PY

# Reinstall broker package if sources changed (editable/host venv install).
if [[ -x /opt/agentproof/.venv-broker/bin/uv ]]; then
  echo "==> refresh broker package"
  /opt/agentproof/.venv-broker/bin/uv pip install -e "$ROOT/apps/broker"
elif [[ -x /opt/agentproof/.venv-broker/bin/pip ]]; then
  /opt/agentproof/.venv-broker/bin/pip install -e "$ROOT/apps/broker"
fi

echo "==> restart broker"
systemctl restart agentproof-broker
sleep 2
systemctl is-active agentproof-broker
# Bind host comes from broker.env; health on VPC IP if set.
BIND="$(grep -E '^BROKER_BIND_HOST=' "$BROKER_ENV" | cut -d= -f2- || true)"
PORT="$(grep -E '^BROKER_PORT=' "$BROKER_ENV" | cut -d= -f2- || echo 8090)"
if [[ -n "$BIND" ]]; then
  curl -sk "https://${BIND}:${PORT}/healthz" || true
  echo
fi
echo "DONE sandbox deploy"
