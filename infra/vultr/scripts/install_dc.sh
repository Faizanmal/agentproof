#!/usr/bin/env bash
# Install short `dc` on the app VM (no symlink execute-bit issues).
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_dc.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TARGET="${1:-/usr/local/bin/dc}"
SRC="$ROOT/infra/vultr/scripts/dc.sh"

if [[ ! -f "$SRC" ]]; then
  echo "missing $SRC" >&2
  exit 1
fi

# Wrapper invokes bash explicitly so the .sh file does not need +x in git.
install -d "$(dirname "$TARGET")"
cat >"$TARGET" <<EOF
#!/bin/bash
exec bash $(printf '%q' "$SRC") "\$@"
EOF
chmod 0755 "$TARGET"
echo "installed $TARGET -> bash $SRC"
"$TARGET" version >/dev/null 2>&1 || true
echo "try: dc ps"
