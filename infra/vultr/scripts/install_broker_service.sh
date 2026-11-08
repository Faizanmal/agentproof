#!/usr/bin/env bash
# Install or refresh the agentproof-broker systemd unit on the sandbox VM.
# Does not rotate secrets, open firewall ports, or change billing.
# Usage:
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_broker_service.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
UNIT_SRC="$ROOT/infra/vultr/systemd/agentproof-broker.service"
UNIT_DST=/etc/systemd/system/agentproof-broker.service
BROKER_ENV="${BROKER_ENV:-/etc/agentproof/broker.env}"
VENV="${BROKER_VENV:-/opt/agentproof/.venv-broker}"

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing unit source: $UNIT_SRC" >&2
  exit 1
fi
if [[ ! -f "$BROKER_ENV" ]]; then
  echo "Missing $BROKER_ENV (create mode 600 with HMAC/TLS/digest first)." >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/agentproof-broker" ]]; then
  echo "Missing $VENV/bin/agentproof-broker." >&2
  echo "Create the broker venv and install the package, for example:" >&2
  echo "  python3 -m venv $VENV" >&2
  echo "  $VENV/bin/pip install -U pip" >&2
  echo "  $VENV/bin/pip install -e $ROOT/apps/broker" >&2
  exit 1
fi

install -d -m 0755 /var/lib/agentproof-broker /var/lib/agentproof-broker/artifacts
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
systemctl enable agentproof-broker.service
systemctl restart agentproof-broker.service
sleep 2
systemctl --no-pager --full status agentproof-broker.service || true
systemctl is-active agentproof-broker.service
echo "Installed and started agentproof-broker.service"
