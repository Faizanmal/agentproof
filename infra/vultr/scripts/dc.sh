#!/usr/bin/env bash
# Drop-in for long:
#   docker compose --env-file /etc/agentproof/release.env -f infra/compose.prod.yml ...
#
# Install once on app VM:
#   sudo ln -sf /opt/agentproof/infra/vultr/scripts/dc.sh /usr/local/bin/dc
# Then:
#   dc ps
#   dc logs -f worker
#   dc --profile execution up -d
set -euo pipefail

# Resolve through /usr/local/bin/dc → …/scripts/dc.sh so ROOT is the repo, not /.
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
if command -v readlink >/dev/null 2>&1; then
  SCRIPT_PATH="$(readlink -f "$SCRIPT_PATH" 2>/dev/null || readlink -n "$SCRIPT_PATH" || echo "$SCRIPT_PATH")"
fi
ROOT="$(cd "$(dirname "$SCRIPT_PATH")/../../.." && pwd)"
RELEASE_ENV="${RELEASE_ENV:-/etc/agentproof/release.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/infra/compose.prod.yml}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "dc: compose file not found: $COMPOSE_FILE (script=$SCRIPT_PATH root=$ROOT)" >&2
  exit 1
fi
if [[ ! -f "$RELEASE_ENV" ]]; then
  echo "dc: missing $RELEASE_ENV" >&2
  exit 1
fi

exec docker compose --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE" "$@"
