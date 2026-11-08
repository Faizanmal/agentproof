#!/usr/bin/env bash
# Drop-in for long:
#   docker compose --env-file /etc/agentproof/release.env -f infra/compose.prod.yml ...
#
# Install once on app VM:
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_dc.sh
# Then: dc ps | dc logs -f worker | dc --profile execution up -d
set -euo pipefail

# When invoked via `bash /path/to/dc.sh` or a wrapper, BASH_SOURCE is this file.
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
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
