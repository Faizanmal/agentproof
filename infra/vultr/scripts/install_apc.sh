#!/usr/bin/env bash
# Install short `apc` on the app VM (avoids clash with GNU /usr/bin/dc).
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_apc.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TARGET="${1:-/usr/local/bin/apc}"
SRC="$ROOT/infra/vultr/scripts/apc.sh"

if [[ ! -f "$SRC" ]]; then
  echo "missing $SRC" >&2
  exit 1
fi

# Drop broken `dc` wrapper if we previously installed one (leaves GNU dc alone).
if [[ -e /usr/local/bin/dc ]] && grep -q 'agentproof.*dc\.sh\|vultr/scripts/dc' /usr/local/bin/dc 2>/dev/null; then
  rm -f /usr/local/bin/dc
  echo "removed conflicting /usr/local/bin/dc (use apc)"
fi

install -d "$(dirname "$TARGET")"
cat >"$TARGET" <<EOF
#!/bin/bash
exec bash $(printf '%q' "$SRC") "\$@"
EOF
chmod 0755 "$TARGET"
echo "installed $TARGET -> bash $SRC"
echo "try: apc ps"
