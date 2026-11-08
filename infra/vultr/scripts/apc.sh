#!/usr/bin/env bash
# Short compose wrapper. Do NOT name the install target `dc` — on Ubuntu
# /usr/bin/dc is GNU desk calculator and hangs waiting for stdin.
#
# Install: sudo bash /opt/agentproof/infra/vultr/scripts/install_apc.sh
# Then:    apc ps | apc logs -f worker | apc --profile execution up -d
set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
ROOT="$(cd "$(dirname "$SCRIPT_PATH")/../../.." && pwd)"
RELEASE_ENV="${RELEASE_ENV:-/etc/agentproof/release.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/infra/compose.prod.yml}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "apc: compose file not found: $COMPOSE_FILE (script=$SCRIPT_PATH root=$ROOT)" >&2
  exit 1
fi
if [[ ! -f "$RELEASE_ENV" ]]; then
  echo "apc: missing $RELEASE_ENV" >&2
  exit 1
fi

exec docker compose --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE" "$@"
