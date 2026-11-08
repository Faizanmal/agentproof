#!/usr/bin/env bash
# Install or refresh the agentproof-broker systemd unit on the sandbox VM.
# Does not rotate secrets, open firewall ports, or change billing.
# Usage:
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_broker_service.sh
#   sudo bash /opt/agentproof/infra/vultr/scripts/install_broker_service.sh --bootstrap-venv
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
UNIT_SRC="$ROOT/infra/vultr/systemd/agentproof-broker.service"
UNIT_DST=/etc/systemd/system/agentproof-broker.service
BROKER_ENV="${BROKER_ENV:-/etc/agentproof/broker.env}"
VENV="${BROKER_VENV:-/opt/agentproof/.venv-broker}"
BOOTSTRAP_VENV=0

for arg in "$@"; do
  case "$arg" in
    --bootstrap-venv) BOOTSTRAP_VENV=1 ;;
    -h|--help)
      echo "Usage: $0 [--bootstrap-venv]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

pick_python() {
  for candidate in python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
      if [[ "$ver" == "3.13" ]]; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

bootstrap_venv() {
  local py
  if ! py="$(pick_python)"; then
    echo "Python 3.13 is required for agentproof-broker (found none)." >&2
    exit 1
  fi
  echo "==> bootstrap broker venv with $py at $VENV"
  "$py" -m venv "$VENV"
  "$VENV/bin/pip" install -U pip hatchling
  "$VENV/bin/pip" install -e "$ROOT/apps/broker"
  test -x "$VENV/bin/agentproof-broker"
}

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing unit source: $UNIT_SRC" >&2
  exit 1
fi
if [[ ! -f "$BROKER_ENV" ]]; then
  echo "Missing $BROKER_ENV (create mode 600 with HMAC/TLS/digest first)." >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/agentproof-broker" ]]; then
  if [[ "$BOOTSTRAP_VENV" -eq 1 ]]; then
    bootstrap_venv
  else
    echo "Missing $VENV/bin/agentproof-broker." >&2
    echo "Bootstrap once with:" >&2
    echo "  sudo bash $0 --bootstrap-venv" >&2
    exit 1
  fi
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
