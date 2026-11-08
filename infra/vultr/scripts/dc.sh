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

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RELEASE_ENV="${RELEASE_ENV:-/etc/agentproof/release.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/infra/compose.prod.yml}"

exec docker compose --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE" "$@"
